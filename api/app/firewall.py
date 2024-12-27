from typing import Dict, List, Optional
import json
from pathlib import Path
import subprocess
import ipaddress
import logging
import uuid

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
        self._init_firewall()
        self._load_rules()

    def _init_firewall(self) -> None:
        try:
            for chain in ['VM-INBOUND', 'VM-OUTBOUND']:
                try:
                    subprocess.run(['sudo', 'iptables', '-N', chain], check=True)
                except subprocess.CalledProcessError:
                    pass

            for chain in ['VM-INBOUND', 'VM-OUTBOUND']:
                subprocess.run(['sudo', 'iptables', '-F', chain], check=True)

            subprocess.run(['sudo', 'iptables', '-P', 'INPUT', 'DROP'], check=True)
            subprocess.run(['sudo', 'iptables', '-P', 'FORWARD', 'DROP'], check=True)
            subprocess.run(['sudo', 'iptables', '-P', 'OUTPUT', 'ACCEPT'], check=True)

            subprocess.run([
                'sudo', 'iptables', '-A', 'INPUT',
                '-m', 'state', '--state', 'ESTABLISHED,RELATED',
                '-j', 'ACCEPT'
            ], check=True)

            subprocess.run([
                'sudo', 'iptables', '-A', 'INPUT',
                '-i', 'lo',
                '-j', 'ACCEPT'
            ], check=True)

            subprocess.run([
                'sudo', 'iptables', '-A', 'INPUT',
                '-j', 'VM-INBOUND'
            ], check=True)
            subprocess.run([
                'sudo', 'iptables', '-A', 'OUTPUT',
                '-j', 'VM-OUTBOUND'
            ], check=True)

            logger.info("Initialized firewall chains and policies")

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
        chain = "VM-INBOUND" if rule.direction == "inbound" else "VM-OUTBOUND"
        cmd = ['-A', chain, '-p', rule.protocol]
        
        ports = rule.port_range.split("-")
        if len(ports) == 1:
            cmd.extend([
                '--dport' if rule.direction == "inbound" else '--sport',
                ports[0]
            ])
        elif len(ports) == 2:
            cmd.extend([
                '-m', 'multiport',
                '--dports' if rule.direction == "inbound" else '--sports',
                rule.port_range
            ])
        
        if rule.direction == "inbound":
            cmd.extend(['-s', rule.source])
        else:
            cmd.extend(['-d', rule.source])
        
        cmd.extend(['-j', 'ACCEPT'])
        cmd.extend(['-m', 'comment', '--comment', f'id:{rule.rule_id}'])
        
        return cmd

    def _apply_rule(self, cluster_id: str, rule: FirewallRule) -> None:
        try:
            cmd_args = self._build_iptables_rule(rule)
            cmd = ['sudo', 'iptables'] + cmd_args
            rule.iptables_rule = ' '.join(cmd_args)
            subprocess.run(cmd, check=True)
            logger.info(f"Applied firewall rule: {' '.join(cmd)}")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to apply firewall rule: {e}")
            raise

    def _remove_rule(self, cluster_id: str, rule: FirewallRule) -> None:
        try:
            if not rule.iptables_rule:
                logger.warning(f"No iptables rule found for {rule.rule_id}")
                return
            
            cmd_args = rule.iptables_rule.replace('-A', '-D', 1).split()
            cmd = ['sudo', 'iptables'] + cmd_args
            subprocess.run(cmd, check=True)
            logger.info(f"Removed firewall rule: {' '.join(cmd)}")
            
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