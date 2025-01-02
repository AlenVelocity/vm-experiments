"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listIPs, addIP, removeIP, listVPCs, attachIP, detachIP, listVMs } from "@/lib/api";
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
  const [selectedVPC, setSelectedVPC] = useState<string>("");
  const [selectedVM, setSelectedVM] = useState<string>("");

  const queryClient = useQueryClient();

  const { data: ipsData, isLoading: isLoadingIPs } = useQuery({
    queryKey: ["ips"],
    queryFn: async () => {
      const response = await listIPs();
      return response.data;
    },
  });

  const { data: vpcsData } = useQuery({
    queryKey: ["vpcs"],
    queryFn: async () => {
      const response = await listVPCs();
      return response.data;
    },
  });

  const { data: vmsData, isLoading: isLoadingVMs } = useQuery({
    queryKey: ["vms"],
    queryFn: async () => {
      const response = await listVMs();
      return response.data;
    },
    enabled: !!selectedVPC,
  });

  const addIPMutation = useMutation({
    mutationFn: (ip: string) => addIP(ip),
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
    mutationFn: ({ vmId, ip }: { vmId: string; ip: string }) =>
      attachIP(vmId, ip),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ips"] });
      queryClient.invalidateQueries({ queryKey: ["vms"] });
    },
  });

  const detachIPMutation = useMutation({
    mutationFn: ({ vmId, ip }: { vmId: string; ip: string }) =>
      detachIP(vmId, ip),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ips"] });
      queryClient.invalidateQueries({ queryKey: ["vms"] });
    },
  });

  const handleAddIP = () => {
    if (!newIP) return;
    addIPMutation.mutate(newIP);
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

      <div className="space-y-4">
        <div className="flex space-x-4">
          <div className="w-64">
            <Label>VPC</Label>
            <Select
              value={selectedVPC}
              onValueChange={(value) => {
                setSelectedVPC(value);
                setSelectedVM("");
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select a VPC" />
              </SelectTrigger>
              <SelectContent>
                {vpcsData?.vpcs?.map((vpc: any) => (
                  <SelectItem key={vpc.name} value={vpc.name}>
                    {vpc.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="w-64">
            <Label>Virtual Machine</Label>
            <Select
              value={selectedVM}
              onValueChange={setSelectedVM}
              disabled={!selectedVPC}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select a VM" />
              </SelectTrigger>
              <SelectContent>
                {vmsData?.vms
                  ?.filter((vm: any) => vm.config.network_name === selectedVPC)
                  .map((vm: any) => (
                    <SelectItem key={vm.id} value={vm.id}>
                      {vm.name}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>

      {isLoadingIPs ? (
        <div>Loading IPs...</div>
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
              <TableRow key={ip.ip}>
                <TableCell>{ip.ip}</TableCell>
                <TableCell>{ip.status}</TableCell>
                <TableCell>{ip.attached_to || "Not attached"}</TableCell>
                <TableCell>
                  {ip.attached_to ? (
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() =>
                        detachIPMutation.mutate({
                          vmId: ip.attached_to,
                          ip: ip.ip,
                        })
                      }
                      disabled={detachIPMutation.isPending}
                    >
                      <Unlink className="h-4 w-4" />
                    </Button>
                  ) : (
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() =>
                        attachIPMutation.mutate({
                          vmId: selectedVM,
                          ip: ip.ip,
                        })
                      }
                      disabled={!selectedVM || attachIPMutation.isPending}
                    >
                      <Link className="h-4 w-4" />
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => removeIPMutation.mutate(ip.ip)}
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