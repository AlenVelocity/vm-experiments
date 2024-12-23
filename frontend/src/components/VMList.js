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
  useToast,
  Heading,
  useDisclosure,
} from '@chakra-ui/react';
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
            <Th>CPU Cores</Th>
            <Th>Memory (MB)</Th>
            <Th>SSH Port</Th>
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
              <Td>{machine.cpu_cores}</Td>
              <Td>{machine.memory_mb}</Td>
              <Td>
                {machine.status === 'running' && machine.ssh_port ? (
                  <Badge colorScheme="blue">
                    localhost:{machine.ssh_port}
                  </Badge>
                ) : '-'}
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