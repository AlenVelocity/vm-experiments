"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listIPs, addIP, removeIP, getClusters, getMachineIPs, attachIP, detachIP } from "@/lib/api";
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Plus, Unlink, Link } from "lucide-react";

export default function IPManagementPage() {
  const [isOpen, setIsOpen] = useState(false);
  const [newIP, setNewIP] = useState("");
  const [selectedCluster, setSelectedCluster] = useState<string>("");
  const [selectedMachine, setSelectedMachine] = useState<string>("");

  const queryClient = useQueryClient();

  const { data: ipsData, isLoading: isLoadingIPs } = useQuery({
    queryKey: ["ips"],
    queryFn: async () => {
      const response = await listIPs();
      return response.data;
    },
  });

  const { data: clustersData } = useQuery({
    queryKey: ["clusters"],
    queryFn: async () => {
      const response = await getClusters();
      return response.data;
    },
  });

  const { data: machineIPsData, isLoading: isLoadingMachineIPs } = useQuery({
    queryKey: ["machine-ips", selectedCluster, selectedMachine],
    queryFn: async () => {
      if (!selectedCluster || !selectedMachine) return { ips: [] };
      const response = await getMachineIPs(selectedCluster, selectedMachine);
      return response.data;
    },
    enabled: !!selectedCluster && !!selectedMachine,
  });

  const addIPMutation = useMutation({
    mutationFn: addIP,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ips"] });
      setIsOpen(false);
      setNewIP("");
    },
  });

  const removeIPMutation = useMutation({
    mutationFn: removeIP,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ips"] });
    },
  });

  const attachIPMutation = useMutation({
    mutationFn: ({ cluster, machine, ip }: { cluster: string; machine: string; ip: string }) =>
      attachIP(cluster, machine, { ip }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ips"] });
      queryClient.invalidateQueries({ queryKey: ["machine-ips"] });
    },
  });

  const detachIPMutation = useMutation({
    mutationFn: ({ cluster, machine, ip }: { cluster: string; machine: string; ip: string }) =>
      detachIP(cluster, machine, ip),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ips"] });
      queryClient.invalidateQueries({ queryKey: ["machine-ips"] });
    },
  });

  const handleAddIP = () => {
    if (!newIP) return;
    addIPMutation.mutate({ ip: newIP });
  };

  return (
    <div className="p-4 space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold">IP Management</h1>
        <Dialog open={isOpen} onOpenChange={setIsOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-2 h-4 w-4" />
              Add IP
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Add IP Address</DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>IP Address</Label>
                <Input
                  value={newIP}
                  onChange={(e) => setNewIP(e.target.value)}
                  placeholder="e.g., 192.168.1.100"
                />
              </div>
              <Button
                onClick={handleAddIP}
                disabled={!newIP || addIPMutation.isPending}
              >
                {addIPMutation.isPending ? "Adding..." : "Add IP"}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </div>

      <div className="flex gap-4 items-center">
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

        <Select
          value={selectedMachine}
          onValueChange={setSelectedMachine}
          disabled={!selectedCluster}
        >
          <SelectTrigger className="w-[200px]">
            <SelectValue placeholder="Select machine" />
          </SelectTrigger>
          <SelectContent>
            {clustersData?.clusters
              ?.find((c: any) => c.name === selectedCluster)
              ?.machines?.map((machine: any) => (
                <SelectItem key={machine.name} value={machine.name}>
                  {machine.name}
                </SelectItem>
              ))}
          </SelectContent>
        </Select>
      </div>

      {isLoadingIPs ? (
        <div>Loading...</div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>IP Address</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Attached To</TableHead>
              <TableHead className="w-[100px]">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {ipsData?.ips?.map((ip: any) => (
              <TableRow key={ip.address}>
                <TableCell>{ip.address}</TableCell>
                <TableCell>{ip.status}</TableCell>
                <TableCell>{ip.attached_to || "Not attached"}</TableCell>
                <TableCell>
                  {ip.attached_to ? (
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() =>
                        detachIPMutation.mutate({
                          cluster: selectedCluster,
                          machine: selectedMachine,
                          ip: ip.address,
                        })
                      }
                      disabled={!selectedCluster || !selectedMachine || detachIPMutation.isPending}
                    >
                      <Unlink className="h-4 w-4" />
                    </Button>
                  ) : (
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() =>
                        attachIPMutation.mutate({
                          cluster: selectedCluster,
                          machine: selectedMachine,
                          ip: ip.address,
                        })
                      }
                      disabled={!selectedCluster || !selectedMachine || attachIPMutation.isPending}
                    >
                      <Link className="h-4 w-4" />
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => removeIPMutation.mutate(ip.address)}
                    disabled={removeIPMutation.isPending}
                    className="ml-2"
                  >
                    <Plus className="h-4 w-4 rotate-45" />
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