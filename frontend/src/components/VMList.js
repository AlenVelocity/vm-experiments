import React, { useState, useEffect } from 'react';
import {
  Box,
  Table,
  Thead,
  Tbody,
  Tr,
  Th,
  Td,
  Button,
  Badge,
  HStack,
  VStack,
  useToast,
  Heading,
  useDisclosure,
  Text,
  Tooltip,
  IconButton,
  Code,
} from '@chakra-ui/react';
import { CopyIcon } from '@chakra-ui/icons';
import VMConsoleModal from './VMConsoleModal';

function VMList({ cluster }) {
  const [machines, setMachines] = useState([]);
  const [selectedVM, setSelectedVM] = useState(null);
  const { isOpen, onOpen, onClose } = useDisclosure();
  const toast = useToast();

  const fetchMachines = async () => {
    try {
      const response = await fetch(`http://localhost:5000/api/clusters/${cluster}/machines`);
      const data = await response.json();
      setMachines(data.machines || []);
    } catch (error) {
      toast({
        title: 'Error fetching machines',
        description: error.message,
        status: 'error',
        duration: 5000,
        isClosable: true,
      });
    }
  };

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text);
    toast({
      title: 'Copied to clipboard',
      status: 'success',
      duration: 2000,
    });
  };

  const handleStart = async (machineName) => {
    try {
      const response = await fetch(`http://localhost:5000/api/clusters/${cluster}/machines/${machineName}/start`, {
        method: 'POST',
      });
      if (response.ok) {
        toast({
          title: 'Machine Started',
          description: `Successfully started machine ${machineName}`,
          status: 'success',
          duration: 3000,
          isClosable: true,
        });
        fetchMachines();
      }
    } catch (error) {
      toast({
        title: 'Error starting machine',
        description: error.message,
        status: 'error',
        duration: 5000,
        isClosable: true,
      });
    }
  };

  const handleStop = async (machineName) => {
    try {
      const response = await fetch(`http://localhost:5000/api/clusters/${cluster}/machines/${machineName}/stop`, {
        method: 'POST',
      });
      if (response.ok) {
        toast({
          title: 'Machine Stopped',
          description: `Successfully stopped machine ${machineName}`,
          status: 'success',
          duration: 3000,
          isClosable: true,
        });
        fetchMachines();
      }
    } catch (error) {
      toast({
        title: 'Error stopping machine',
        description: error.message,
        status: 'error',
        duration: 5000,
        isClosable: true,
      });
    }
  };

  const handleRestart = async (machineName) => {
    try {
      const response = await fetch(`http://localhost:5000/api/clusters/${cluster}/machines/${machineName}/restart`, {
        method: 'POST',
      });
      if (response.ok) {
        toast({
          title: 'Machine Restarted',
          description: `Successfully restarted machine ${machineName}`,
          status: 'success',
          duration: 3000,
          isClosable: true,
        });
        fetchMachines();
      }
    } catch (error) {
      toast({
        title: 'Error restarting machine',
        description: error.message,
        status: 'error',
        duration: 5000,
        isClosable: true,
      });
    }
  };

  const handleTerminate = async (machineName) => {
    if (window.confirm(`Are you sure you want to terminate ${machineName}? This action cannot be undone.`)) {
      try {
        const response = await fetch(`http://localhost:5000/api/clusters/${cluster}/machines/${machineName}/terminate`, {
          method: 'POST',
        });
        if (response.ok) {
          toast({
            title: 'Machine Terminated',
            description: `Successfully terminated machine ${machineName}`,
            status: 'success',
            duration: 3000,
            isClosable: true,
          });
          fetchMachines();
        }
      } catch (error) {
        toast({
          title: 'Error terminating machine',
          description: error.message,
          status: 'error',
          duration: 5000,
          isClosable: true,
        });
      }
    }
  };

  const handleOpenConsole = async (machineName) => {
    try {
      const response = await fetch(`http://localhost:5000/api/clusters/${cluster}/machines/${machineName}/serial-console`);
      const data = await response.json();
      if (data.console_url) {
        setSelectedVM(machineName);
        onOpen();
      }
    } catch (error) {
      toast({
        title: 'Error opening console',
        description: error.message,
        status: 'error',
        duration: 5000,
        isClosable: true,
      });
    }
  };

  useEffect(() => {
    fetchMachines();
    const interval = setInterval(fetchMachines, 5000);
    return () => clearInterval(interval);
  }, [cluster]);

  return (
    <Box width="100%" maxW="1200px" mx="auto" p={4}>
      <HStack justify="space-between" mb={6}>
        <Heading size="lg">Machines</Heading>
      </HStack>

      <Table variant="simple">
        <Thead>
          <Tr>
            <Th>Name</Th>
            <Th>Status</Th>
            <Th>Resources</Th>
            <Th>Network</Th>
            <Th>Connections</Th>
            <Th>Actions</Th>
          </Tr>
        </Thead>
        <Tbody>
          {machines.map((machine) => (
            <Tr key={machine.id}>
              <Td>{machine.name}</Td>
              <Td>
                <Badge
                  colorScheme={machine.status === 'running' ? 'green' : 'gray'}
                >
                  {machine.status}
                </Badge>
              </Td>
              <Td>
                <VStack align="start" spacing={1}>
                  <Text fontSize="sm">CPU: {machine.cpu_cores} cores</Text>
                  <Text fontSize="sm">Memory: {machine.memory_mb} MB</Text>
                </VStack>
              </Td>
              <Td>
                <VStack align="start" spacing={1}>
                  {machine.network_interfaces && Object.entries(machine.network_interfaces).map(([name, info]) => (
                    <Box key={name} p={2} borderWidth="1px" borderRadius="md" width="100%">
                      <Text fontSize="sm" fontWeight="bold" color="gray.600">
                        {info.network_name}
                      </Text>
                      {info.private_ip && (
                        <VStack align="start" spacing={0}>
                          <HStack>
                            <Text fontSize="sm" fontWeight="medium">Private IP:</Text>
                            <Code fontSize="sm">{info.private_ip}</Code>
                          </HStack>
                          <Text fontSize="xs" color="gray.500">
                            Subnet: {info.subnet_mask} • Gateway: {info.gateway}
                          </Text>
                        </VStack>
                      )}
                      {info.public_ip && (
                        <VStack align="start" spacing={0}>
                          <HStack>
                            <Text fontSize="sm" fontWeight="medium">Public IP:</Text>
                            <Code fontSize="sm">{info.public_ip}</Code>
                          </HStack>
                          <Text fontSize="xs" color="gray.500">
                            Subnet: {info.subnet_mask} • Gateway: {info.gateway}
                          </Text>
                        </VStack>
                      )}
                      {info.mac && (
                        <HStack>
                          <Text fontSize="sm" fontWeight="medium">MAC:</Text>
                          <Code fontSize="sm">{info.mac}</Code>
                        </HStack>
                      )}
                      {info.forwarded_ports && info.forwarded_ports.map((port, idx) => (
                        <Text key={idx} fontSize="xs" color="gray.600">
                          Port forwarding: {port.guest_port} → {port.host_port}
                        </Text>
                      ))}
                    </Box>
                  ))}
                </VStack>
              </Td>
              <Td>
                {machine.status === 'running' && machine.connection_info && (
                  <VStack align="start" spacing={2}>
                    {machine.connection_info.public_ssh && (
                      <HStack>
                        <Text fontSize="sm" fontWeight="medium">Public:</Text>
                        <Code fontSize="sm">{machine.connection_info.public_ssh}</Code>
                        <IconButton
                          aria-label="Copy public SSH command"
                          icon={<CopyIcon />}
                          size="sm"
                          onClick={() => copyToClipboard(machine.connection_info.public_ssh)}
                        />
                      </HStack>
                    )}
                    {machine.connection_info.ssh && (
                      <HStack>
                        <Text fontSize="sm" fontWeight="medium">Local:</Text>
                        <Code fontSize="sm">{machine.connection_info.ssh}</Code>
                        <IconButton
                          aria-label="Copy SSH command"
                          icon={<CopyIcon />}
                          size="sm"
                          onClick={() => copyToClipboard(machine.connection_info.ssh)}
                        />
                      </HStack>
                    )}
                    {machine.connection_info.vnc && (
                      <HStack>
                        <Text fontSize="sm" fontWeight="medium">VNC:</Text>
                        <Code fontSize="sm">{machine.connection_info.vnc}</Code>
                        <IconButton
                          aria-label="Copy VNC address"
                          icon={<CopyIcon />}
                          size="sm"
                          onClick={() => copyToClipboard(machine.connection_info.vnc)}
                        />
                      </HStack>
                    )}
                  </VStack>
                )}
                {(!machine.status || machine.status !== 'running' || !machine.connection_info) && (
                  <Text fontSize="sm" color="gray.500">
                    Not available
                  </Text>
                )}
              </Td>
              <Td>
                <HStack spacing={2}>
                  {machine.status === 'running' ? (
                    <>
                      <Button
                        colorScheme="red"
                        size="sm"
                        onClick={() => handleStop(machine.name)}
                      >
                        Stop
                      </Button>
                      <Button
                        colorScheme="yellow"
                        size="sm"
                        onClick={() => handleRestart(machine.name)}
                      >
                        Restart
                      </Button>
                      <Button
                        colorScheme="blue"
                        size="sm"
                        onClick={() => handleOpenConsole(machine.name)}
                      >
                        Console
                      </Button>
                    </>
                  ) : (
                    <Button
                      colorScheme="green"
                      size="sm"
                      onClick={() => handleStart(machine.name)}
                    >
                      Start
                    </Button>
                  )}
                  <Button
                    colorScheme="red"
                    size="sm"
                    variant="outline"
                    onClick={() => handleTerminate(machine.name)}
                  >
                    Terminate
                  </Button>
                </HStack>
              </Td>
            </Tr>
          ))}
        </Tbody>
      </Table>

      <VMConsoleModal
        isOpen={isOpen}
        onClose={onClose}
        vmName={selectedVM}
      />
    </Box>
  );
}

export default VMList; 