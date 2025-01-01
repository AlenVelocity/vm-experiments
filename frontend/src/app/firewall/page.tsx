"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getFirewallRules, createFirewallRule, deleteFirewallRule, getClusters } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Plus, Trash2 } from "lucide-react";

export default function FirewallPage() {
  const [isOpen, setIsOpen] = useState(false);
  const [selectedCluster, setSelectedCluster] = useState<string>("");
  const [rule, setRule] = useState({
    direction: "inbound",
    protocol: "tcp",
    port_range: "",
    source: "",
    description: "",
  });

  const queryClient = useQueryClient();

  const { data: clustersData } = useQuery({
    queryKey: ["clusters"],
    queryFn: async () => {
      const response = await getClusters();
      return response.data;
    },
  });

  const { data: rulesData, isLoading } = useQuery({
    queryKey: ["firewall-rules", selectedCluster],
    queryFn: async () => {
      if (!selectedCluster) return { rules: [] };
      const response = await getFirewallRules(selectedCluster);
      return response.data;
    },
    enabled: !!selectedCluster,
  });

  const createRuleMutation = useMutation({
    mutationFn: (data: typeof rule) => createFirewallRule(selectedCluster, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["firewall-rules", selectedCluster] });
      setIsOpen(false);
      setRule({
        direction: "inbound",
        protocol: "tcp",
        port_range: "",
        source: "",
        description: "",
      });
    },
  });

  const deleteRuleMutation = useMutation({
    mutationFn: (ruleId: string) => deleteFirewallRule(selectedCluster, ruleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["firewall-rules", selectedCluster] });
    },
  });

  const handleCreate = () => {
    if (!selectedCluster || !rule.port_range || !rule.source) return;
    createRuleMutation.mutate(rule);
  };

  return (
    <div className="p-4 space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold">Firewall Rules</h1>
        <div className="flex items-center gap-4">
          <Select value={selectedCluster} onValueChange={setSelectedCluster}>
            <SelectTrigger className="w-[200px]">
              <SelectValue placeholder="Select cluster" />
            </SelectTrigger>
            <SelectContent>
              {clustersData?.clusters?.map((cluster: any) => (
                <SelectItem key={cluster.name} value={cluster.name}>
                  {cluster.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Dialog open={isOpen} onOpenChange={setIsOpen}>
            <DialogTrigger asChild>
              <Button disabled={!selectedCluster}>
                <Plus className="mr-2 h-4 w-4" />
                Add Rule
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Add Firewall Rule</DialogTitle>
              </DialogHeader>
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label>Direction</Label>
                  <Select
                    value={rule.direction}
                    onValueChange={(value) => setRule({ ...rule, direction: value })}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="inbound">Inbound</SelectItem>
                      <SelectItem value="outbound">Outbound</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Protocol</Label>
                  <Select
                    value={rule.protocol}
                    onValueChange={(value) => setRule({ ...rule, protocol: value })}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="tcp">TCP</SelectItem>
                      <SelectItem value="udp">UDP</SelectItem>
                      <SelectItem value="icmp">ICMP</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Port Range</Label>
                  <Input
                    value={rule.port_range}
                    onChange={(e) => setRule({ ...rule, port_range: e.target.value })}
                    placeholder="e.g., 80 or 80-443"
                  />
                </div>
                <div className="space-y-2">
                  <Label>Source/Destination</Label>
                  <Input
                    value={rule.source}
                    onChange={(e) => setRule({ ...rule, source: e.target.value })}
                    placeholder="e.g., 0.0.0.0/0 or IP address"
                  />
                </div>
                <div className="space-y-2">
                  <Label>Description</Label>
                  <Input
                    value={rule.description}
                    onChange={(e) => setRule({ ...rule, description: e.target.value })}
                    placeholder="Rule description"
                  />
                </div>
                <Button
                  onClick={handleCreate}
                  disabled={!rule.port_range || !rule.source || createRuleMutation.isPending}
                >
                  {createRuleMutation.isPending ? "Adding..." : "Add Rule"}
                </Button>
              </div>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {!selectedCluster ? (
        <div className="text-center text-muted-foreground">Select a cluster to view firewall rules</div>
      ) : isLoading ? (
        <div>Loading...</div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Direction</TableHead>
              <TableHead>Protocol</TableHead>
              <TableHead>Port Range</TableHead>
              <TableHead>Source/Destination</TableHead>
              <TableHead>Description</TableHead>
              <TableHead className="w-[100px]">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rulesData?.rules?.map((rule: any) => (
              <TableRow key={rule.id}>
                <TableCell className="capitalize">{rule.direction}</TableCell>
                <TableCell className="uppercase">{rule.protocol}</TableCell>
                <TableCell>{rule.port_range}</TableCell>
                <TableCell>{rule.source}</TableCell>
                <TableCell>{rule.description}</TableCell>
                <TableCell>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => deleteRuleMutation.mutate(rule.id)}
                    disabled={deleteRuleMutation.isPending}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
} 