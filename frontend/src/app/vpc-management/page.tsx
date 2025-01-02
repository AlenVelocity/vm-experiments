"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { createVPC, listVPCs, deleteVPC } from "@/lib/api";
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
import { Plus, Trash } from "lucide-react";

export default function VPCManagementPage() {
  const [isOpen, setIsOpen] = useState(false);
  const [newVPC, setNewVPC] = useState({
    name: "",
    cidr: "192.168.0.0/16"
  });

  const queryClient = useQueryClient();

  const { data: vpcsData, isLoading } = useQuery({
    queryKey: ["vpcs"],
    queryFn: async () => {
      const response = await listVPCs();
      return response.data;
    },
  });

  const createVPCMutation = useMutation({
    mutationFn: createVPC,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vpcs"] });
      setIsOpen(false);
      setNewVPC({ name: "", cidr: "192.168.0.0/16" });
    },
  });

  const deleteVPCMutation = useMutation({
    mutationFn: deleteVPC,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vpcs"] });
    },
  });

  const handleCreateVPC = () => {
    if (!newVPC.name) return;
    createVPCMutation.mutate(newVPC);
  };

  return (
    <div className="p-4 space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold">VPC Management</h1>
        <Dialog open={isOpen} onOpenChange={setIsOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-2 h-4 w-4" />
              Create VPC
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create VPC</DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>Name</Label>
                <Input
                  value={newVPC.name}
                  onChange={(e) => setNewVPC({ ...newVPC, name: e.target.value })}
                  placeholder="e.g., production-vpc"
                />
              </div>
              <div className="space-y-2">
                <Label>CIDR Block</Label>
                <Input
                  value={newVPC.cidr}
                  onChange={(e) => setNewVPC({ ...newVPC, cidr: e.target.value })}
                  placeholder="e.g., 192.168.0.0/16"
                />
              </div>
              <Button
                onClick={handleCreateVPC}
                disabled={!newVPC.name || createVPCMutation.isPending}
              >
                {createVPCMutation.isPending ? "Creating..." : "Create VPC"}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </div>

      {isLoading ? (
        <div>Loading VPCs...</div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>CIDR Block</TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {vpcsData?.vpcs?.map((vpc: any) => (
              <TableRow key={vpc.name}>
                <TableCell>{vpc.name}</TableCell>
                <TableCell>{vpc.cidr}</TableCell>
                <TableCell>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => deleteVPCMutation.mutate(vpc.name)}
                    disabled={deleteVPCMutation.isPending}
                  >
                    <Trash className="h-4 w-4" />
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