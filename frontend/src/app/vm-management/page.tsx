"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  createVM,
  listVPCs,
  attachDisk,
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
    network_name: "",
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
  const { data: vpcsData } = useQuery({
    queryKey: ["vpcs"],
    queryFn: listVPCs,
  });

  // Fetch available disks
  const { data: disksData } = useQuery({
    queryKey: ["disks"],
    queryFn: listDisks,
  });

  // Fetch VMs
  const { data: vmsData, isLoading: isLoadingVMs, error: vmsError } = useQuery({
    queryKey: ["vms"],
    queryFn: listVMs,
  });

  // Fetch available images
  const { data: imagesData } = useQuery({
    queryKey: ["images"],
    queryFn: listImages,
  });

  // Create VM mutation
  const createVMMutation = useMutation({
    mutationFn: createVM,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vms"] });
      setIsCreateOpen(false);
      setNewVM({
        name: "",
        network_name: "",
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

  const handleCreateVM = () => {
    createVMMutation.mutate(newVM);
  };

  const isCreateButtonDisabled = () => {
    return (
      !newVM.name ||
      !newVM.network_name ||
      !newVM.image_id ||
      newVM.cpu_cores < 1 ||
      newVM.memory_mb < 512 ||
      newVM.disk_size_gb < 10 ||
      createVMMutation.isPending
    );
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

  if (isLoadingVMs) {
    return (
      <div className="flex items-center justify-center h-screen">
        <LoadingSpinner className="h-8 w-8" />
      </div>
    );
  }

  if (vmsError) {
    return (
      <div className="p-4">
        <ErrorMessage message="Failed to load VMs. Please try again later." />
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
                  required
                />
              </div>
              <div className="space-y-2">
                <Label>VPC</Label>
                <Select
                  value={newVM.network_name}
                  onValueChange={(value) =>
                    setNewVM({ ...newVM, network_name: value })
                  }
                  required
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select a VPC" />
                  </SelectTrigger>
                  <SelectContent>
                    {vpcsData?.vpcs?.map((vpc) => (
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
                  required
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select an image" />
                  </SelectTrigger>
                  <SelectContent>
                    {imagesData?.images?.map((image) => (
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
                        cpu_cores: parseInt(e.target.value) || 1,
                      })
                    }
                    min={1}
                    max={16}
                    required
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
                        memory_mb: parseInt(e.target.value) || 512,
                      })
                    }
                    min={512}
                    max={32768}
                    step={512}
                    required
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
                        disk_size_gb: parseInt(e.target.value) || 10,
                      })
                    }
                    min={10}
                    max={1000}
                    required
                  />
                </div>
              </div>
              <Button
                onClick={handleCreateVM}
                disabled={isCreateButtonDisabled()}
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
        {vmsData?.vms?.map((vm) => (
          <Card key={vm.id}>
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                <span>{vm.name}</span>
                <span className="text-sm font-normal">
                  Status: {vm.status || "unknown"}
                </span>
              </CardTitle>
              <CardDescription>
                {vm.config.cpu_cores} CPU cores, {vm.config.memory_mb}MB RAM,{" "}
                {vm.config.disk_size_gb}GB Disk
                {vm.network_info?.private && (
                  <div>Private IP: {vm.network_info.private.ip}</div>
                )}
                {vm.network_info?.public && (
                  <div>Public IP: {vm.network_info.public.ip}</div>
                )}
                {vm.ssh_port && <div>SSH Port: {vm.ssh_port}</div>}
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
                  {disksData?.disks?.map((disk) => (
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