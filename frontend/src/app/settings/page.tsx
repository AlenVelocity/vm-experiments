"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function SettingsPage() {
  const [apiUrl, setApiUrl] = useState("http://localhost:5000/api");

  const handleSave = () => {
    // Save API URL to localStorage or context
    localStorage.setItem("apiUrl", apiUrl);
    window.location.reload();
  };

  return (
    <div className="p-4 space-y-4">
      <h1 className="text-2xl font-bold">Settings</h1>

      <Card>
        <CardHeader>
          <CardTitle>API Configuration</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>API URL</Label>
              <Input
                value={apiUrl}
                onChange={(e) => setApiUrl(e.target.value)}
                placeholder="http://localhost:5000/api"
              />
              <p className="text-sm text-muted-foreground">
                The base URL for the VM Manager API
              </p>
            </div>
            <Button onClick={handleSave}>Save Changes</Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
} 