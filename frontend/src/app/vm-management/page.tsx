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
  const { data: vpcsData } = useQuery({
    queryKey: ["vpcs"],
    queryFn: async () => {
      const response = await listVPCs();
      return response.data;
    },
  });

  // Fetch available disks
  const { data: disksData } = useQuery({
    queryKey: ["disks"],
    queryFn: async () => {
      const response = await listDisks();
      return response.data;
    },
  });

  // Fetch VMs
  const { data: vmsData, isLoading: isLoadingVMs } = useQuery({
    queryKey: ["vms"],
    queryFn: async () => {
      const response = await listVMs();
      return response.data;
    },
  });

  // Fetch available images
  const { data: imagesData } = useQuery({
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
    mutationFn: ({ vmId, ip }: { vmId: string; ip?: string }) =>
      attachIP(vmId, ip!),
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
    createVMMutation.mutate(newVM);
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
    setIsAttachDiskOpen(false);
    setSelectedDisk(null);
  };

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
                    <SelectValue placeholder="Select an Ubuntu image" />
                  </SelectTrigger>
                  <SelectContent>
                    {imagesData?.images?.map((image: any) => (
                      <SelectItem key={image.id} value={image.id}>
                        {image.name} ({new Date(image.date).toLocaleDateString()})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
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
                      memory_mb: parseInt(e.target.value) || 1024,
                    })
                  }
                  min={1024}
                  step={1024}
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
                      disk_size_gb: parseInt(e.target.value) || 20,
                    })
                  }
                  min={20}
                />
              </div>
              <Button
                onClick={handleCreateVM}
                disabled={
                  !newVM.name ||
                  !newVM.vpc ||
                  !newVM.image_id ||
                  createVMMutation.isPending
                }
              >
                {createVMMutation.isPending ? "Creating..." : "Create VM"}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </div>

      {/* VM List */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {isLoadingVMs ? (
          <div>Loading VMs...</div>
        ) : (
          vmsData?.vms?.map((vm: any) => (
            <Card key={vm.id} className="relative">
              <CardHeader>
                <CardTitle className="flex items-center justify-between">
                  <span>{vm.name}</span>
                  <span className={`text-sm px-2 py-1 rounded-full ${
                    vm.status === "running"
                      ? "bg-green-100 text-green-800"
                      : vm.status === "stopped"
                      ? "bg-red-100 text-red-800"
                      : "bg-gray-100 text-gray-800"
                  }`}>
                    {vm.status}
                  </span>
                </CardTitle>
                <CardDescription>
                  VPC: {vm.config.network_name}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {/* Resource Information */}
                <div className="space-y-2">
                  <div className="flex items-center space-x-2">
                    <Cpu className="h-4 w-4" />
                    <span>CPU Cores: {vm.config.cpu_cores}</span>
                  </div>
                  <div className="flex items-center space-x-2">
                    <div className="h-4 w-4" /> {/* Memory icon */}
                    <span>Memory: {vm.config.memory_mb}MB</span>
                  </div>
                </div>

                {/* Network Information */}
                <div className="space-y-2">
                  <h4 className="font-semibold flex items-center space-x-2">
                    <Network className="h-4 w-4" />
                    <span>Network</span>
                  </h4>
                  {vm.network_info?.private && (
                    <div className="text-sm">
                      Private IP: {vm.network_info.private.ip}
                    </div>
                  )}
                  {vm.network_info?.public && (
                    <div className="text-sm">
                      Public IP: {vm.network_info.public.ip}
                      <Button
                        variant="ghost"
                        size="sm"
                        className="ml-2"
                        onClick={() =>
                          detachIPMutation.mutate({
                            vmId: vm.id,
                            ip: vm.network_info.public.ip,
                          })
                        }
                      >
                        Detach
                      </Button>
                    </div>
                  )}
                  {!vm.network_info?.public && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        attachIPMutation.mutate({
                          vmId: vm.id,
                        })
                      }
                    >
                      Attach Public IP
                    </Button>
                  )}
                </div>

                {/* Attached Disks */}
                <div className="space-y-2">
                  <h4 className="font-semibold flex items-center space-x-2">
                    <HardDrive className="h-4 w-4" />
                    <span>Disks</span>
                  </h4>
                  {vm.disks?.map((disk: any) => (
                    <div key={disk.disk_id} className="text-sm flex items-center justify-between">
                      <span>{disk.name} ({disk.size_gb}GB)</span>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() =>
                          detachDiskMutation.mutate({
                            vmId: vm.id,
                            diskId: disk.disk_id,
                          })
                        }
                      >
                        Detach
                      </Button>
                    </div>
                  ))}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setSelectedVM(vm.id);
                      setIsAttachDiskOpen(true);
                    }}
                  >
                    Attach Disk
                  </Button>
                </div>

                {/* Actions */}
                <div className="flex space-x-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setSelectedVM(vm.id);
                      setResizeConfig({
                        cpu_cores: vm.config.cpu_cores,
                        memory_mb: vm.config.memory_mb,
                      });
                      setIsResizeOpen(true);
                    }}
                  >
                    <RefreshCw className="h-4 w-4 mr-2" />
                    Resize
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={async () => {
                      const response = await getVMConsole(vm.id);
                      // Handle console connection
                      console.log(response.data);
                    }}
                  >
                    Console
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))
        )}
      </div>

      {/* Resize VM Dialog */}
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
                    cpu_cores: parseInt(e.target.value) || 0,
                  })
                }
                min={1}
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
                    memory_mb: parseInt(e.target.value) || 0,
                  })
                }
                min={1024}
                step={1024}
              />
            </div>
            <Button
              onClick={handleResizeVM}
              disabled={
                !selectedVM ||
                (!resizeConfig.cpu_cores && !resizeConfig.memory_mb) ||
                resizeVMMutation.isPending
              }
            >
              {resizeVMMutation.isPending ? "Resizing..." : "Resize VM"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Attach Disk Dialog */}
      <Dialog open={isAttachDiskOpen} onOpenChange={setIsAttachDiskOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Attach Disk</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Select Disk</Label>
              <Select
                value={selectedDisk || ""}
                onValueChange={(value) => setSelectedDisk(value)}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select a disk" />
                </SelectTrigger>
                <SelectContent>
                  {disksData?.disks
                    ?.filter((disk: any) => !disk.attached_to)
                    .map((disk: any) => (
                      <SelectItem key={disk.disk_id} value={disk.disk_id}>
                        {disk.name} ({disk.size_gb}GB)
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              onClick={handleAttachDisk}
              disabled={!selectedDisk || attachDiskMutation.isPending}
            >
              {attachDiskMutation.isPending ? "Attaching..." : "Attach Disk"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
} 