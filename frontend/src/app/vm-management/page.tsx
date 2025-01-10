"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  createVM,
  listVPCs,
  attachDisk,
  detachDisk,
  attachIP,
  detachIP,
  resizeVM,
  getVMConsole,
  getVMMetrics,
  listDisks,
  listVMs,
  listImages,
} from "@/lib/api";
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
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ErrorMessage } from "@/components/ui/error-message";
import { LoadingSpinner } from "@/components/ui/loading-spinner";
import { Plus, HardDrive, Network, Cpu, RefreshCw } from "lucide-react";

export default function VMManagementPage() {
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [isResizeOpen, setIsResizeOpen] = useState(false);
  const [isAttachDiskOpen, setIsAttachDiskOpen] = useState(false);
  const [selectedVM, setSelectedVM] = useState<string | null>(null);
  const [selectedDisk, setSelectedDisk] = useState<string | null>(null);
  const [newVM, setNewVM] = useState({
    name: "",
    vpc: "",
    cpu_cores: 2,
    memory_mb: 2048,
    disk_size_gb: 20,
    image_id: "",
  });
  const [resizeConfig, setResizeConfig] = useState({
    cpu_cores: 0,
    memory_mb: 0,
  });

  const queryClient = useQueryClient();

  // Fetch VPCs for VM creation
  const { data: vpcsData, isLoading: isLoadingVPCs, error: vpcsError } = useQuery({
    queryKey: ["vpcs"],
    queryFn: async () => {
      const response = await listVPCs();
      return response.data;
    },
  });

  // Fetch available disks
  const { data: disksData, isLoading: isLoadingDisks, error: disksError } = useQuery({
    queryKey: ["disks"],
    queryFn: async () => {
      const response = await listDisks();
      return response.data;
    },
  });

  // Fetch VMs
  const { data: vmsData, isLoading: isLoadingVMs, error: vmsError } = useQuery({
    queryKey: ["vms"],
    queryFn: async () => {
      const response = await listVMs();
      return response.data;
    },
  });

  // Fetch available images
  const { data: imagesData, isLoading: isLoadingImages, error: imagesError } = useQuery({
    queryKey: ["images"],
    queryFn: async () => {
      const response = await listImages();
      return response.data;
    },
  });

  // Create VM mutation
  const createVMMutation = useMutation({
    mutationFn: createVM,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vms"] });
      setIsCreateOpen(false);
      setNewVM({
        name: "",
        vpc: "",
        cpu_cores: 2,
        memory_mb: 2048,
        disk_size_gb: 20,
        image_id: "",
      });
    },
  });

  // Resize VM mutation
  const resizeVMMutation = useMutation({
    mutationFn: ({ vmId, data }: { vmId: string; data: any }) =>
      resizeVM(vmId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vms"] });
      setIsResizeOpen(false);
      setResizeConfig({ cpu_cores: 0, memory_mb: 0 });
    },
  });

  // Attach disk mutation
  const attachDiskMutation = useMutation({
    mutationFn: ({ vmId, diskId }: { vmId: string; diskId: string }) =>
      attachDisk(vmId, diskId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vms"] });
      setIsAttachDiskOpen(false);
      setSelectedDisk(null);
    },
  });

  // Detach disk mutation
  const detachDiskMutation = useMutation({
    mutationFn: ({ vmId, diskId }: { vmId: string; diskId: string }) =>
      detachDisk(vmId, diskId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vms"] });
    },
  });

  // Attach IP mutation
  const attachIPMutation = useMutation({
    mutationFn: ({ vmId, ip }: { vmId: string; ip: string }) =>
      attachIP(vmId, ip),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vms"] });
    },
  });

  // Detach IP mutation
  const detachIPMutation = useMutation({
    mutationFn: ({ vmId, ip }: { vmId: string; ip: string }) =>
      detachIP(vmId, ip),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vms"] });
    },
  });

  const handleCreateVM = () => {
    if (!newVM.name || !newVM.vpc || !newVM.image_id) return;
    createVMMutation.mutate({
      name: newVM.name,
      vpc: newVM.vpc,
      cpu_cores: newVM.cpu_cores,
      memory_mb: newVM.memory_mb,
      disk_size_gb: newVM.disk_size_gb,
      image_id: newVM.image_id
    });
  };

  const handleResizeVM = () => {
    if (!selectedVM || (!resizeConfig.cpu_cores && !resizeConfig.memory_mb)) return;
    resizeVMMutation.mutate({
      vmId: selectedVM,
      data: resizeConfig,
    });
  };

  const handleAttachDisk = () => {
    if (!selectedVM || !selectedDisk) return;
    attachDiskMutation.mutate({
      vmId: selectedVM,
      diskId: selectedDisk,
    });
  };

  if (isLoadingVMs || isLoadingVPCs || isLoadingDisks || isLoadingImages) {
    return (
      <div className="flex items-center justify-center h-screen">
        <LoadingSpinner className="h-8 w-8" />
      </div>
    );
  }

  if (vmsError || vpcsError || disksError || imagesError) {
    return (
      <div className="p-4">
        <ErrorMessage message="Failed to load data. Please try again later." />
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold">VM Management</h1>
        <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-2 h-4 w-4" />
              Create VM
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create Virtual Machine</DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>Name</Label>
                <Input
                  value={newVM.name}
                  onChange={(e) =>
                    setNewVM({ ...newVM, name: e.target.value })
                  }
                  placeholder="e.g., web-server-1"
                />
              </div>
              <div className="space-y-2">
                <Label>VPC</Label>
                <Select
                  value={newVM.vpc}
                  onValueChange={(value) =>
                    setNewVM({ ...newVM, vpc: value })
                  }
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
              <div className="space-y-2">
                <Label>Ubuntu Image</Label>
                <Select
                  value={newVM.image_id}
                  onValueChange={(value) =>
                    setNewVM({ ...newVM, image_id: value })
                  }
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select an image" />
                  </SelectTrigger>
                  <SelectContent>
                    {imagesData?.images?.map((image: any) => (
                      <SelectItem key={image.id} value={image.id}>
                        {image.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid grid-cols-3 gap-4">
                <div className="space-y-2">
                  <Label>CPU Cores</Label>
                  <Input
                    type="number"
                    value={newVM.cpu_cores}
                    onChange={(e) =>
                      setNewVM({
                        ...newVM,
                        cpu_cores: parseInt(e.target.value),
                      })
                    }
                    min={1}
                    max={16}
                  />
                </div>
                <div className="space-y-2">
                  <Label>Memory (MB)</Label>
                  <Input
                    type="number"
                    value={newVM.memory_mb}
                    onChange={(e) =>
                      setNewVM({
                        ...newVM,
                        memory_mb: parseInt(e.target.value),
                      })
                    }
                    min={512}
                    max={32768}
                    step={512}
                  />
                </div>
                <div className="space-y-2">
                  <Label>Disk Size (GB)</Label>
                  <Input
                    type="number"
                    value={newVM.disk_size_gb}
                    onChange={(e) =>
                      setNewVM({
                        ...newVM,
                        disk_size_gb: parseInt(e.target.value),
                      })
                    }
                    min={10}
                    max={1000}
                  />
                </div>
              </div>
              <Button
                onClick={handleCreateVM}
                disabled={createVMMutation.isPending}
                className="w-full"
              >
                {createVMMutation.isPending ? (
                  <LoadingSpinner className="mr-2" />
                ) : (
                  <Plus className="mr-2 h-4 w-4" />
                )}
                Create VM
              </Button>
              {createVMMutation.isError && (
                <ErrorMessage
                  message={
                    createVMMutation.error?.message ||
                    "Failed to create VM. Please try again."
                  }
                />
              )}
            </div>
          </DialogContent>
        </Dialog>
      </div>

      <div className="grid gap-4">
        {vmsData?.vms?.map((vm: any) => (
          <Card key={vm.id}>
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                <span>{vm.name}</span>
                <span className="text-sm font-normal">
                  Status: {vm.status}
                </span>
              </CardTitle>
              <CardDescription>
                {vm.cpu_cores} CPU cores, {vm.memory_mb}MB RAM,{" "}
                {vm.disk_size_gb}GB Disk
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setSelectedVM(vm.id);
                    setIsResizeOpen(true);
                  }}
                >
                  <Cpu className="mr-2 h-4 w-4" />
                  Resize
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setSelectedVM(vm.id);
                    setIsAttachDiskOpen(true);
                  }}
                >
                  <HardDrive className="mr-2 h-4 w-4" />
                  Attach Disk
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    // TODO: Implement IP management
                  }}
                >
                  <Network className="mr-2 h-4 w-4" />
                  Manage IPs
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Dialog open={isResizeOpen} onOpenChange={setIsResizeOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Resize Virtual Machine</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>CPU Cores</Label>
              <Input
                type="number"
                value={resizeConfig.cpu_cores}
                onChange={(e) =>
                  setResizeConfig({
                    ...resizeConfig,
                    cpu_cores: parseInt(e.target.value),
                  })
                }
                min={1}
                max={16}
              />
            </div>
            <div className="space-y-2">
              <Label>Memory (MB)</Label>
              <Input
                type="number"
                value={resizeConfig.memory_mb}
                onChange={(e) =>
                  setResizeConfig({
                    ...resizeConfig,
                    memory_mb: parseInt(e.target.value),
                  })
                }
                min={512}
                max={32768}
                step={512}
              />
            </div>
            <Button
              onClick={handleResizeVM}
              disabled={resizeVMMutation.isPending}
              className="w-full"
            >
              {resizeVMMutation.isPending ? (
                <LoadingSpinner className="mr-2" />
              ) : (
                <RefreshCw className="mr-2 h-4 w-4" />
              )}
              Resize VM
            </Button>
            {resizeVMMutation.isError && (
              <ErrorMessage
                message={
                  resizeVMMutation.error?.message ||
                  "Failed to resize VM. Please try again."
                }
              />
            )}
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={isAttachDiskOpen} onOpenChange={setIsAttachDiskOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Attach Disk</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Disk</Label>
              <Select
                value={selectedDisk || ""}
                onValueChange={setSelectedDisk}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select a disk" />
                </SelectTrigger>
                <SelectContent>
                  {disksData?.disks?.map((disk: any) => (
                    <SelectItem key={disk.id} value={disk.id}>
                      {disk.name} ({disk.size_gb}GB)
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              onClick={handleAttachDisk}
              disabled={attachDiskMutation.isPending}
              className="w-full"
            >
              {attachDiskMutation.isPending ? (
                <LoadingSpinner className="mr-2" />
              ) : (
                <HardDrive className="mr-2 h-4 w-4" />
              )}
              Attach Disk
            </Button>
            {attachDiskMutation.isError && (
              <ErrorMessage
                message={
                  attachDiskMutation.error?.message ||
                  "Failed to attach disk. Please try again."
                }
              />
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
} 