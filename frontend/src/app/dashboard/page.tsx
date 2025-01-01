"use client";

import { useQuery } from "@tanstack/react-query";
import { getClusters } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Server, Shield, HardDrive, Network } from "lucide-react";

export default function DashboardPage() {
  const { data: clustersData } = useQuery({
    queryKey: ["clusters"],
    queryFn: async () => {
      const response = await getClusters();
      return response.data;
    },
  });

  const stats = [
    {
      title: "Total Clusters",
      value: clustersData?.clusters?.length || 0,
      icon: Server,
    },
    {
      title: "Active Firewalls",
      value: "N/A",
      icon: Shield,
    },
    {
      title: "Total Disks",
      value: "N/A",
      icon: HardDrive,
    },
    {
      title: "IP Addresses",
      value: "N/A",
      icon: Network,
    },
  ];

  return (
    <div className="p-4 space-y-4">
      <h1 className="text-2xl font-bold">Dashboard</h1>
      
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {stats.map((stat) => (
          <Card key={stat.title}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">
                {stat.title}
              </CardTitle>
              <stat.icon className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{stat.value}</div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
} 