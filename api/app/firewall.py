from typing import Dict, List, Optional
import json
from pathlib import Path
import subprocess
import ipaddress
import logging
import uuid
import platform
import os

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
        self.pf_rule = None

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "direction": self.direction,
            "protocol": self.protocol,
            "port_range": self.port_range,
            "source": self.source,
            "description": self.description,
            "pf_rule": self.pf_rule
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
        rule.pf_rule = data.get("pf_rule")
        return rule

class FirewallManager:
    def __init__(self):
        self.rules_dir = Path("firewall")
        self.rules_dir.mkdir(parents=True, exist_ok=True)
        self.rules: Dict[str, Dict[str, FirewallRule]] = {}
        self.pf_conf_path = "/etc/pf.conf"
        self.pf_anchor = "vm-rules"
        self._init_firewall()
        self._load_rules()

    def _init_firewall(self) -> None:
        try:
            # Create a backup of the original pf.conf if it doesn't exist
            if not os.path.exists(f"{self.pf_conf_path}.backup"):
                subprocess.run(['sudo', 'cp', self.pf_conf_path, f"{self.pf_conf_path}.backup"], check=True)

            # Add our anchor to pf.conf if it's not already there
            try:
                conf_content = subprocess.run(['sudo', 'cat', self.pf_conf_path], check=True, capture_output=True, text=True).stdout
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to read pf.conf: {e}")
                raise

            if f"anchor \"{self.pf_anchor}\"" not in conf_content:
                # Create a temporary file with the new content
                temp_conf = Path("/tmp/pf.conf.tmp")
                temp_conf.write_text(conf_content + f"\nanchor \"{self.pf_anchor}\"\n")
                
                # Use sudo to copy the temporary file to pf.conf
                subprocess.run(['sudo', 'cp', str(temp_conf), self.pf_conf_path], check=True)

            # Enable PF if it's not already enabled
            subprocess.run(['sudo', 'pfctl', '-e'], check=False)  # Ignore if already enabled
            
            # Create initial anchor rules
            initial_rules = """# VM Rules
# Default policy
block return in all
block return out all

# Allow established connections
pass in quick proto tcp from any to any flags S/SA keep state
pass out quick proto tcp from any to any flags S/SA keep state

# Allow basic outbound connectivity
pass out quick proto tcp from any to any keep state
pass out quick proto udp from any to any keep state
pass out quick proto icmp from any to any keep state
"""
            
            # Write initial rules to a temporary file
            temp_rules_file = Path("/tmp/pf_initial_rules")
            temp_rules_file.write_text(initial_rules)
            
            # Load the rules into our anchor
            subprocess.run(['sudo', 'pfctl', '-a', self.pf_anchor, '-f', str(temp_rules_file)], check=True)
            
            logger.info("Initialized PF firewall rules")

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
                    if rule.pf_rule:
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

    def _build_pf_rule(self, rule: FirewallRule) -> str:
        direction = "in" if rule.direction == "inbound" else "out"
        action = "pass"
        
        # Handle port range
        ports = rule.port_range.split("-")
        if len(ports) == 1:
            port_spec = f"port {ports[0]}"
        else:
            port_spec = f"port {ports[0]}:{ports[1]}"
        
        # Build the rule
        pf_rule = f"{action} {direction} proto {rule.protocol} from {rule.source} to any {port_spec}"
        
        # Add rule ID as a comment
        pf_rule += f" # id:{rule.rule_id}"
        
        return pf_rule

    def _apply_rule(self, cluster_id: str, rule: FirewallRule) -> None:
        try:
            # Build the PF rule
            pf_rule = self._build_pf_rule(rule)
            rule.pf_rule = pf_rule
            
            # Write all rules to a temporary file
            temp_rules_file = Path("/tmp/pf_rules")
            all_rules = []
            for cluster_rules in self.rules.values():
                for r in cluster_rules.values():
                    if r.pf_rule:
                        all_rules.append(r.pf_rule)
            
            temp_rules_file.write_text("\n".join(all_rules))
            
            # Load the rules into our anchor
            subprocess.run(['sudo', 'pfctl', '-a', self.pf_anchor, '-f', str(temp_rules_file)], check=True)
            
            logger.info(f"Applied firewall rule: {pf_rule}")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to apply firewall rule: {e}")
            raise

    def _remove_rule(self, cluster_id: str, rule: FirewallRule) -> None:
        try:
            if not rule.pf_rule:
                logger.warning(f"No PF rule found for {rule.rule_id}")
                return
            
            # Remove the rule by reapplying all rules except this one
            rule.pf_rule = None
            
            # Write remaining rules to a temporary file
            temp_rules_file = Path("/tmp/pf_rules")
            all_rules = []
            for cluster_rules in self.rules.values():
                for r in cluster_rules.values():
                    if r.pf_rule:
                        all_rules.append(r.pf_rule)
            
            temp_rules_file.write_text("\n".join(all_rules))
            
            # Load the rules into our anchor
            subprocess.run(['sudo', 'pfctl', '-a', self.pf_anchor, '-f', str(temp_rules_file)], check=True)
            
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
        if cluster_id not in self.rules or rule_id not in self.rules[cluster_id]:
            return None
        return self.rules[cluster_id][rule_id] 