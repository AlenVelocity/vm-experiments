"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { createDisk, listDisks } from "@/lib/api";
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
import { Plus, HardDrive } from "lucide-react";

export default function DiskManagementPage() {
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [newDisk, setNewDisk] = useState({
    name: "",
    size_gb: 20,
  });

  const queryClient = useQueryClient();

  // Fetch disks
  const { data: disksData, isLoading, error } = useQuery({
    queryKey: ["disks"],
    queryFn: async () => {
      const response = await listDisks();
      return response.data;
    },
  });

  // Create disk mutation
  const createDiskMutation = useMutation({
    mutationFn: createDisk,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["disks"] });
      setIsCreateOpen(false);
      setNewDisk({
        name: "",
        size_gb: 20,
      });
    },
  });

  const handleCreateDisk = () => {
    if (!newDisk.name) return;
    createDiskMutation.mutate(newDisk);
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
        <ErrorMessage message="Failed to load disks. Please try again later." />
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold">Disk Management</h1>
        <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-2 h-4 w-4" />
              Create Disk
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create Disk</DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>Name</Label>
                <Input
                  value={newDisk.name}
                  onChange={(e) =>
                    setNewDisk({ ...newDisk, name: e.target.value })
                  }
                  placeholder="e.g., data-disk-1"
                />
              </div>
              <div className="space-y-2">
                <Label>Size (GB)</Label>
                <Input
                  type="number"
                  value={newDisk.size_gb}
                  onChange={(e) =>
                    setNewDisk({
                      ...newDisk,
                      size_gb: parseInt(e.target.value),
                    })
                  }
                  min={10}
                  max={1000}
                />
              </div>
              <Button
                onClick={handleCreateDisk}
                disabled={createDiskMutation.isPending}
                className="w-full"
              >
                {createDiskMutation.isPending ? (
                  <LoadingSpinner className="mr-2" />
                ) : (
                  <Plus className="mr-2 h-4 w-4" />
                )}
                Create Disk
              </Button>
              {createDiskMutation.isError && (
                <ErrorMessage
                  message={
                    createDiskMutation.error?.message ||
                    "Failed to create disk. Please try again."
                  }
                />
              )}
            </div>
          </DialogContent>
        </Dialog>
      </div>

      <div className="grid gap-4">
        {disksData?.disks?.map((disk: any) => (
          <Card key={disk.id}>
            <CardHeader>
              <CardTitle className="flex items-center">
                <HardDrive className="mr-2 h-4 w-4" />
                <span>{disk.name}</span>
              </CardTitle>
              <CardDescription>
                Size: {disk.size_gb}GB
                {disk.attached_to && ` • Attached to: ${disk.attached_to}`}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    // TODO: Implement disk resize
                  }}
                >
                  Resize
                </Button>
                {!disk.attached_to && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      // TODO: Implement disk deletion
                    }}
                  >
                    Delete
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
} 