"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listDisks,
  createDisk,
  deleteDisk,
  attachDisk,
  detachDisk,
  resizeDisk,
  getClusters,
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
import { Plus, Trash2, Link, Unlink, ArrowUpDown } from "lucide-react";

export default function DisksPage() {
  const [isOpen, setIsOpen] = useState(false);
  const [isResizeOpen, setIsResizeOpen] = useState(false);
  const [isAttachOpen, setIsAttachOpen] = useState(false);
  const [selectedDisk, setSelectedDisk] = useState<string>("");
  const [newDisk, setNewDisk] = useState({
    name: "",
    size_gb: "",
  });
  const [newSize, setNewSize] = useState("");
  const [selectedMachine, setSelectedMachine] = useState("");

  const queryClient = useQueryClient();

  const { data: disksData, isLoading } = useQuery({
    queryKey: ["disks"],
    queryFn: async () => {
      const response = await listDisks();
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

  const createDiskMutation = useMutation({
    mutationFn: createDisk,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["disks"] });
      setIsOpen(false);
      setNewDisk({ name: "", size_gb: "" });
    },
  });

  const deleteDiskMutation = useMutation({
    mutationFn: deleteDisk,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["disks"] });
    },
  });

  const attachDiskMutation = useMutation({
    mutationFn: ({ diskId, vmName }: { diskId: string; vmName: string }) =>
      attachDisk(diskId, { vm_name: vmName }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["disks"] });
      setIsAttachOpen(false);
      setSelectedMachine("");
    },
  });

  const detachDiskMutation = useMutation({
    mutationFn: detachDisk,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["disks"] });
    },
  });

  const resizeDiskMutation = useMutation({
    mutationFn: ({ diskId, size }: { diskId: string; size: number }) =>
      resizeDisk(diskId, { size_gb: size }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["disks"] });
      setIsResizeOpen(false);
      setNewSize("");
    },
  });

  const handleCreateDisk = () => {
    if (!newDisk.name || !newDisk.size_gb) return;
    createDiskMutation.mutate({
      name: newDisk.name,
      size_gb: parseInt(newDisk.size_gb),
    });
  };

  const handleResizeDisk = () => {
    if (!selectedDisk || !newSize) return;
    resizeDiskMutation.mutate({
      diskId: selectedDisk,
      size: parseInt(newSize),
    });
  };

  const handleAttachDisk = () => {
    if (!selectedDisk || !selectedMachine) return;
    attachDiskMutation.mutate({
      diskId: selectedDisk,
      vmName: selectedMachine,
    });
  };

  return (
    <div className="p-4 space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold">Disks</h1>
        <Dialog open={isOpen} onOpenChange={setIsOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-2 h-4 w-4" />
              Create Disk
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create New Disk</DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>Name</Label>
                <Input
                  value={newDisk.name}
                  onChange={(e) => setNewDisk({ ...newDisk, name: e.target.value })}
                  placeholder="my-disk"
                />
              </div>
              <div className="space-y-2">
                <Label>Size (GB)</Label>
                <Input
                  type="number"
                  value={newDisk.size_gb}
                  onChange={(e) => setNewDisk({ ...newDisk, size_gb: e.target.value })}
                  placeholder="20"
                />
              </div>
              <Button
                onClick={handleCreateDisk}
                disabled={!newDisk.name || !newDisk.size_gb || createDiskMutation.isPending}
              >
                {createDiskMutation.isPending ? "Creating..." : "Create"}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </div>

      {isLoading ? (
        <div>Loading...</div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Size (GB)</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Attached To</TableHead>
              <TableHead className="w-[150px]">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {disksData?.disks?.map((disk: any) => (
              <TableRow key={disk.id}>
                <TableCell>{disk.name}</TableCell>
                <TableCell>{disk.size_gb}</TableCell>
                <TableCell>{disk.status}</TableCell>
                <TableCell>{disk.attached_to || "Not attached"}</TableCell>
                <TableCell className="space-x-2">
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => {
                      setSelectedDisk(disk.id);
                      setIsResizeOpen(true);
                    }}
                  >
                    <ArrowUpDown className="h-4 w-4" />
                  </Button>
                  {disk.attached_to ? (
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => detachDiskMutation.mutate(disk.id)}
                      disabled={detachDiskMutation.isPending}
                    >
                      <Unlink className="h-4 w-4" />
                    </Button>
                  ) : (
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => {
                        setSelectedDisk(disk.id);
                        setIsAttachOpen(true);
                      }}
                    >
                      <Link className="h-4 w-4" />
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => deleteDiskMutation.mutate(disk.id)}
                    disabled={deleteDiskMutation.isPending}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <Dialog open={isResizeOpen} onOpenChange={setIsResizeOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Resize Disk</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>New Size (GB)</Label>
              <Input
                type="number"
                value={newSize}
                onChange={(e) => setNewSize(e.target.value)}
                placeholder="Enter new size in GB"
              />
            </div>
            <Button
              onClick={handleResizeDisk}
              disabled={!newSize || resizeDiskMutation.isPending}
            >
              {resizeDiskMutation.isPending ? "Resizing..." : "Resize"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={isAttachOpen} onOpenChange={setIsAttachOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Attach Disk to Machine</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Select Machine</Label>
              <Select value={selectedMachine} onValueChange={setSelectedMachine}>
                <SelectTrigger>
                  <SelectValue placeholder="Select a machine" />
                </SelectTrigger>
                <SelectContent>
                  {clustersData?.clusters?.flatMap((cluster: any) =>
                    (cluster.machines || []).map((machine: any) => (
                      <SelectItem key={machine.name} value={machine.name}>
                        {machine.name}
                      </SelectItem>
                    ))
                  )}
                </SelectContent>
              </Select>
            </div>
            <Button
              onClick={handleAttachDisk}
              disabled={!selectedMachine || attachDiskMutation.isPending}
            >
              {attachDiskMutation.isPending ? "Attaching..." : "Attach"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
} 