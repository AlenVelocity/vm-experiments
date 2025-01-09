import logging
import json
from pathlib import Path
import subprocess
import ipaddress
import uuid
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

class FirewallRule:
    def __init__(self, rule_id: str, direction: str, protocol: str, 
                 port_range: str, source: str, description: str = ""):
        self.rule_id = rule_id
        self.direction = direction
        self.protocol = protocol
        self.port_range = port_range
        self.source = source
        self.description = description
        self.iptables_rule = None

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "direction": self.direction,
            "protocol": self.protocol,
            "port_range": self.port_range,
            "source": self.source,
            "description": self.description,
            "iptables_rule": self.iptables_rule
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'FirewallRule':
        rule = cls(
            rule_id=data["rule_id"],
            direction=data["direction"],
            protocol=data["protocol"],
            port_range=data["port_range"],
            source=data["source"],
            description=data.get("description", "")
        )
        rule.iptables_rule = data.get("iptables_rule")
        return rule

class FirewallManager:
    def __init__(self):
        self.rules_dir = Path("firewall")
        self.rules_dir.mkdir(parents=True, exist_ok=True)
        self.rules: Dict[str, Dict[str, FirewallRule]] = {}
        self.chain_prefix = "VM_RULES"
        self._init_firewall()
        self._load_rules()

    def _init_firewall(self) -> None:
        try:
            # Create our custom chains if they don't exist
            for direction in ['INPUT', 'OUTPUT']:
                chain_name = f"{self.chain_prefix}_{direction}"
                
                # Check if chain exists
                result = subprocess.run(['sudo', 'iptables', '-L', chain_name], 
                                     capture_output=True, text=True)
                
                if result.returncode != 0:
                    # Create chain
                    subprocess.run(['sudo', 'iptables', '-N', chain_name], check=True)
                    
                    # Add jump rule to built-in chain if not exists
                    subprocess.run(['sudo', 'iptables', '-C', direction, '-j', chain_name],
                                 capture_output=True)
                    if result.returncode != 0:
                        subprocess.run(['sudo', 'iptables', '-I', direction, '1', '-j', chain_name],
                                     check=True)

            logger.info("Initialized iptables firewall rules")

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to initialize firewall: {e}")
            raise

    def _load_rules(self) -> None:
        for file in self.rules_dir.glob("*.json"):
            cluster_id = file.stem
            with open(file) as f:
                rules_data = json.load(f)
                self.rules[cluster_id] = {}
                for rule_id, rule_data in rules_data.items():
                    rule = FirewallRule.from_dict(rule_data)
                    self.rules[cluster_id][rule_id] = rule
                    if rule.iptables_rule:
                        try:
                            self._apply_rule(cluster_id, rule)
                        except Exception as e:
                            logger.error(f"Failed to apply rule {rule_id}: {e}")

    def _save_rules(self, cluster_id: str) -> None:
        rules_file = self.rules_dir / f"{cluster_id}.json"
        with open(rules_file, "w") as f:
            json.dump(
                {rule_id: rule.to_dict() for rule_id, rule in self.rules[cluster_id].items()},
                f,
                indent=2
            )

    def _build_iptables_rule(self, rule: FirewallRule) -> List[str]:
        # Determine chain based on direction
        chain = f"{self.chain_prefix}_INPUT" if rule.direction == "inbound" else f"{self.chain_prefix}_OUTPUT"
        
        # Handle port range
        ports = rule.port_range.split("-")
        if len(ports) == 1:
            port_spec = f"--dport {ports[0]}"
        else:
            port_spec = f"--dport {ports[0]}:{ports[1]}"
        
        # Build the base rule
        iptables_rule = [
            'sudo', 'iptables',
            '-A', chain,
            '-p', rule.protocol,
            '-s', rule.source,
            port_spec,
            '-m', 'comment',
            '--comment', f"id:{rule.rule_id}",
            '-j', 'ACCEPT'
        ]
        
        return iptables_rule

    def _apply_rule(self, cluster_id: str, rule: FirewallRule) -> None:
        try:
            # Build the iptables rule
            iptables_rule = self._build_iptables_rule(rule)
            rule.iptables_rule = ' '.join(iptables_rule)
            
            # Apply the rule
            subprocess.run(iptables_rule, check=True)
            
            logger.info(f"Applied firewall rule: {rule.iptables_rule}")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to apply firewall rule: {e}")
            raise

    def _remove_rule(self, cluster_id: str, rule: FirewallRule) -> None:
        try:
            if not rule.iptables_rule:
                logger.warning(f"No iptables rule found for {rule.rule_id}")
                return
            
            # Convert the stored rule string to list and change -A to -D
            rule_parts = rule.iptables_rule.split()
            rule_parts[rule_parts.index('-A')] = '-D'
            
            # Remove the rule
            subprocess.run(rule_parts, check=True)
            rule.iptables_rule = None
            
            logger.info(f"Removed firewall rule: {rule.rule_id}")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to remove firewall rule: {e}")
            raise

    def create_rule(self, cluster_id: str, direction: str, protocol: str,
                   port_range: str, source: str, description: str = "") -> FirewallRule:
        if direction not in ["inbound", "outbound"]:
            raise ValueError("Direction must be 'inbound' or 'outbound'")
        
        if protocol not in ["tcp", "udp", "icmp"]:
            raise ValueError("Protocol must be 'tcp', 'udp', or 'icmp'")
        
        try:
            ipaddress.ip_network(source)
        except ValueError:
            raise ValueError(f"Invalid CIDR format for source: {source}")

        ports = port_range.split("-")
        for port in ports:
            try:
                port_num = int(port)
                if not (1 <= port_num <= 65535):
                    raise ValueError()
            except ValueError:
                raise ValueError(f"Invalid port number: {port}")
        if len(ports) == 2 and int(ports[0]) >= int(ports[1]):
            raise ValueError("Invalid port range: start port must be less than end port")

        if cluster_id not in self.rules:
            self.rules[cluster_id] = {}

        rule_id = str(uuid.uuid4())[:8]
        rule = FirewallRule(rule_id, direction, protocol, port_range, source, description)
        self._apply_rule(cluster_id, rule)
        self.rules[cluster_id][rule_id] = rule
        self._save_rules(cluster_id)
        return rule

    def delete_rule(self, cluster_id: str, rule_id: str) -> None:
        if cluster_id not in self.rules or rule_id not in self.rules[cluster_id]:
            raise ValueError(f"Rule {rule_id} not found in cluster {cluster_id}")

        rule = self.rules[cluster_id][rule_id]
        self._remove_rule(cluster_id, rule)
        del self.rules[cluster_id][rule_id]
        self._save_rules(cluster_id)

    def list_rules(self, cluster_id: str) -> List[dict]:
        if cluster_id not in self.rules:
            return []
        return [rule.to_dict() for rule in self.rules[cluster_id].values()]

    def get_rule(self, cluster_id: str, rule_id: str) -> Optional[FirewallRule]:
        return self.rules.get(cluster_id, {}).get(rule_id) 