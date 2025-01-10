"use client";

import { useEffect, useRef } from "react";
import { useSocket } from "@/components/providers/socket-provider";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

interface ConsoleProps {
  vmId: string;
}

export function Console({ vmId }: ConsoleProps) {
  const { socket, isConnected } = useSocket();
  const terminalRef = useRef<HTMLDivElement>(null);
  const terminalInstance = useRef<Terminal | null>(null);

  useEffect(() => {
    if (!terminalRef.current || !socket || !isConnected) return;

    // Initialize terminal
    const terminal = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: "monospace",
      theme: {
        background: "#1a1b26",
        foreground: "#a9b1d6",
        cursor: "#c0caf5",
        selectionBackground: "#33467c",
        black: "#32344a",
        red: "#f7768e",
        green: "#9ece6a",
        yellow: "#e0af68",
        blue: "#7aa2f7",
        magenta: "#ad8ee6",
        cyan: "#449dab",
        white: "#787c99",
        brightBlack: "#444b6a",
        brightRed: "#ff7a93",
        brightGreen: "#b9f27c",
        brightYellow: "#ff9e64",
        brightBlue: "#7da6ff",
        brightMagenta: "#bb9af7",
        brightCyan: "#0db9d7",
        brightWhite: "#acb0d0",
      },
    });

    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(terminalRef.current);
    fitAddon.fit();

    terminalInstance.current = terminal;

    // Connect to VM console
    socket.emit("console.connect", { vmName: vmId });

    // Handle console output
    socket.on("console.output", (data: { text: string }) => {
      terminal.write(data.text);
    });

    // Handle console input
    terminal.onData((data: string) => {
      socket.emit("console.input", { text: data });
    });

    // Handle window resize
    const handleResize = () => {
      fitAddon.fit();
    };
    window.addEventListener("resize", handleResize);

    // Handle console disconnection
    socket.on("console.disconnected", () => {
      terminal.write("\r\n\x1b[31mConsole disconnected\x1b[0m\r\n");
    });

    // Handle console errors
    socket.on("console.error", (data: { error: string }) => {
      terminal.write(`\r\n\x1b[31mError: ${data.error}\x1b[0m\r\n`);
    });

    return () => {
      window.removeEventListener("resize", handleResize);
      socket.off("console.output");
      socket.off("console.disconnected");
      socket.off("console.error");
      terminal.dispose();
    };
  }, [socket, isConnected, vmId]);

  return (
    <div className="w-full h-[500px] bg-[#1a1b26] rounded-lg overflow-hidden">
      <div ref={terminalRef} className="w-full h-full" />
    </div>
  );
} 