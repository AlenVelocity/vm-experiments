"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getClusters, createCluster } from "@/lib/api";
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
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Plus } from "lucide-react";

export default function ClustersPage() {
  const [isOpen, setIsOpen] = useState(false);
  const [name, setName] = useState("");
  const [cidr, setCidr] = useState("192.168.0.0/16");
  
  const queryClient = useQueryClient();

  const { data: clustersData, isLoading } = useQuery({
    queryKey: ["clusters"],
    queryFn: async () => {
      const response = await getClusters();
      return response.data;
    },
  });

  const createClusterMutation = useMutation({
    mutationFn: createCluster,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["clusters"] });
      setIsOpen(false);
      setName("");
      setCidr("192.168.0.0/16");
    },
  });

  const handleCreate = () => {
    if (!name) return;
    createClusterMutation.mutate({ name, cidr });
  };

  return (
    <div className="p-4 space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold">Clusters</h1>
        <Dialog open={isOpen} onOpenChange={setIsOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-2 h-4 w-4" />
              Create Cluster
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create New Cluster</DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="name">Name</Label>
                <Input
                  id="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="my-cluster"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="cidr">CIDR</Label>
                <Input
                  id="cidr"
                  value={cidr}
                  onChange={(e) => setCidr(e.target.value)}
                  placeholder="192.168.0.0/16"
                />
              </div>
              <Button
                onClick={handleCreate}
                disabled={!name || createClusterMutation.isPending}
              >
                {createClusterMutation.isPending ? "Creating..." : "Create"}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </div>

      {isLoading ? (
        <div>Loading...</div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {clustersData?.clusters?.map((cluster: any) => (
            <Card key={cluster.name}>
              <CardHeader>
                <CardTitle>{cluster.name}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  <div>
                    <span className="font-medium">CIDR:</span> {cluster.cidr}
                  </div>
                  <div>
                    <span className="font-medium">Used Private IPs:</span>{" "}
                    {cluster.used_private_ips?.length || 0}
                  </div>
                  <div>
                    <span className="font-medium">Used Public IPs:</span>{" "}
                    {cluster.used_public_ips?.length || 0}
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
} 