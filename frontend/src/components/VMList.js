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

function VMList() {
  const [vms, setVMs] = useState([]);
  const [selectedVM, setSelectedVM] = useState(null);
  const { isOpen, onOpen, onClose } = useDisclosure();
  const toast = useToast();

  const fetchVMs = async () => {
    try {
      const response = await fetch('http://localhost:5000/api/vms/list');
      const data = await response.json();
      setVMs(data.vms || []);
    } catch (error) {
      toast({
        title: 'Error fetching VMs',
        description: error.message,
        status: 'error',
        duration: 5000,
        isClosable: true,
      });
    }
  };

  const handleStart = async (vmName) => {
    try {
      const response = await fetch(`http://localhost:5000/api/vms/${vmName}/start`, {
        method: 'POST',
      });
      if (response.ok) {
        toast({
          title: 'VM Started',
          description: `Successfully started VM ${vmName}`,
          status: 'success',
          duration: 3000,
          isClosable: true,
        });
        fetchVMs();
      }
    } catch (error) {
      toast({
        title: 'Error starting VM',
        description: error.message,
        status: 'error',
        duration: 5000,
        isClosable: true,
      });
    }
  };

  const handleStop = async (vmName) => {
    try {
      const response = await fetch(`http://localhost:5000/api/vms/${vmName}/stop`, {
        method: 'POST',
      });
      if (response.ok) {
        toast({
          title: 'VM Stopped',
          description: `Successfully stopped VM ${vmName}`,
          status: 'success',
          duration: 3000,
          isClosable: true,
        });
        fetchVMs();
      }
    } catch (error) {
      toast({
        title: 'Error stopping VM',
        description: error.message,
        status: 'error',
        duration: 5000,
        isClosable: true,
      });
    }
  };

  const handleOpenConsole = (vmName) => {
    setSelectedVM(vmName);
    onOpen();
  };

  useEffect(() => {
    fetchVMs();
    const interval = setInterval(fetchVMs, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <Box width="100%" maxW="1200px" mx="auto" p={4}>
      <HStack justify="space-between" mb={6}>
        <Heading size="lg">Virtual Machines</Heading>
      </HStack>

      <Table variant="simple">
        <Thead>
          <Tr>
            <Th>Name</Th>
            <Th>VPC</Th>
            <Th>Private IP</Th>
            <Th>Public IP</Th>
            <Th>SSH Port</Th>
            <Th>Status</Th>
            <Th>Actions</Th>
          </Tr>
        </Thead>
        <Tbody>
          {vms.map((vm) => (
            <Tr key={vm.name}>
              <Td>{vm.name}</Td>
              <Td>{vm.vpc}</Td>
              <Td>{vm.private_ip}</Td>
              <Td>{vm.public_ip}</Td>
              <Td>
                {vm.status === 'running' && vm.ssh_port ? (
                  <Badge colorScheme="blue">
                    localhost:{vm.ssh_port}
                  </Badge>
                ) : '-'}
              </Td>
              <Td>
                <Badge
                  colorScheme={vm.status === 'running' ? 'green' : 'gray'}
                >
                  {vm.status}
                </Badge>
              </Td>
              <Td>
                <HStack spacing={2}>
                  {vm.status === 'running' ? (
                    <>
                      <Button
                        colorScheme="red"
                        size="sm"
                        onClick={() => handleStop(vm.name)}
                      >
                        Stop
                      </Button>
                      <Button
                        colorScheme="blue"
                        size="sm"
                        onClick={() => handleOpenConsole(vm.name)}
                      >
                        Console
                      </Button>
                    </>
                  ) : (
                    <Button
                      colorScheme="green"
                      size="sm"
                      onClick={() => handleStart(vm.name)}
                    >
                      Start
                    </Button>
                  )}
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