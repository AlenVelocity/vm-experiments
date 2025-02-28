"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { createVPC, listVPCs } from "@/lib/api";
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
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ErrorMessage } from "@/components/ui/error-message";
import { LoadingSpinner } from "@/components/ui/loading-spinner";
import { Plus, Network } from "lucide-react";

export default function VPCManagementPage() {
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [newVPC, setNewVPC] = useState({
    name: "",
    cidr: "10.0.0.0/16",
  });

  const queryClient = useQueryClient();

  // Fetch VPCs
  const { data: vpcsData, isLoading, error } = useQuery({
    queryKey: ["vpcs"],
    queryFn: async () => {
      const response = await listVPCs();
      return response.data;
    },
  });

  // Create VPC mutation
  const createVPCMutation = useMutation({
    mutationFn: createVPC,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vpcs"] });
      setIsCreateOpen(false);
      setNewVPC({
        name: "",
        cidr: "10.0.0.0/16",
      });
    },
  });

  const handleCreateVPC = () => {
    if (!newVPC.name || !newVPC.cidr) return;
    createVPCMutation.mutate(newVPC);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <LoadingSpinner className="h-8 w-8" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4">
        <ErrorMessage message="Failed to load VPCs. Please try again later." />
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold">VPC Management</h1>
        <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-2 h-4 w-4" />
              Create VPC
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create Virtual Private Cloud</DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>Name</Label>
                <Input
                  value={newVPC.name}
                  onChange={(e) =>
                    setNewVPC({ ...newVPC, name: e.target.value })
                  }
                  placeholder="e.g., production-vpc"
                />
              </div>
              <div className="space-y-2">
                <Label>CIDR Block</Label>
                <Input
                  value={newVPC.cidr}
                  onChange={(e) =>
                    setNewVPC({ ...newVPC, cidr: e.target.value })
                  }
                  placeholder="e.g., 10.0.0.0/16"
                />
              </div>
              <Button
                onClick={handleCreateVPC}
                disabled={createVPCMutation.isPending}
                className="w-full"
              >
                {createVPCMutation.isPending ? (
                  <LoadingSpinner className="mr-2" />
                ) : (
                  <Plus className="mr-2 h-4 w-4" />
                )}
                Create VPC
              </Button>
              {createVPCMutation.isError && (
                <ErrorMessage
                  message={
                    createVPCMutation.error?.message ||
                    "Failed to create VPC. Please try again."
                  }
                />
              )}
            </div>
          </DialogContent>
        </Dialog>
      </div>

      <div className="grid gap-4">
        {vpcsData?.vpcs?.map((vpc: any) => (
          <Card key={vpc.id}>
            <CardHeader>
              <CardTitle className="flex items-center">
                <Network className="mr-2 h-4 w-4" />
                <span>{vpc.name}</span>
              </CardTitle>
              <CardDescription>
                CIDR: {vpc.cidr}
                {vpc.vm_count > 0 && ` • VMs: ${vpc.vm_count}`}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    // TODO: Implement VPC deletion (only if no VMs are attached)
                  }}
                >
                  Delete
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
} 