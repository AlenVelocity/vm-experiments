import React, { useEffect, useRef } from 'react';
import { Box, useColorModeValue } from '@chakra-ui/react';
import { Terminal } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';
import { WebLinksAddon } from 'xterm-addon-web-links';
import io from 'socket.io-client';
import 'xterm/css/xterm.css';

function VMConsole({ vmName, isOpen }) {
  const terminalRef = useRef(null);
  const terminalInstanceRef = useRef(null);
  const socketRef = useRef(null);
  const bg = useColorModeValue('gray.50', 'gray.900');

  useEffect(() => {
    if (isOpen && !terminalInstanceRef.current) {
      // Initialize terminal
      const term = new Terminal({
        cursorBlink: true,
        theme: {
          background: bg,
          foreground: '#2D3748',
        },
      });

      // Add addons
      const fitAddon = new FitAddon();
      term.loadAddon(fitAddon);
      term.loadAddon(new WebLinksAddon());

      // Connect to WebSocket
      const socket = io('http://localhost:5000', {
        query: { vmName },
      });

      socket.on('connect', () => {
        console.log('Connected to console server');
      });

      socket.on('output', (data) => {
        term.write(data);
      });

      socket.on('disconnect', () => {
        term.write('\r\nDisconnected from console.\r\n');
      });

      // Handle terminal input
      term.onData((data) => {
        socket.emit('input', data);
      });

      // Mount terminal
      term.open(terminalRef.current);
      fitAddon.fit();

      // Store references
      terminalInstanceRef.current = term;
      socketRef.current = socket;

      // Handle window resize
      const handleResize = () => fitAddon.fit();
      window.addEventListener('resize', handleResize);

      return () => {
        window.removeEventListener('resize', handleResize);
        if (socketRef.current) {
          socketRef.current.disconnect();
        }
        if (terminalInstanceRef.current) {
          terminalInstanceRef.current.dispose();
          terminalInstanceRef.current = null;
        }
      };
    }
  }, [isOpen, vmName, bg]);

  return (
    <Box
      ref={terminalRef}
      w="100%"
      h="400px"
      borderRadius="md"
      overflow="hidden"
      bg={bg}
    />
  );
}

export default VMConsole; 