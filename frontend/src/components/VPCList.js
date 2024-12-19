import React, { useState, useEffect } from 'react';
import {
  Box,
  Button,
  Table,
  Thead,
  Tbody,
  Tr,
  Th,
  Td,
  useToast,
  Heading,
  HStack,
} from '@chakra-ui/react';
import CreateVPCModal from './CreateVPCModal';

function VPCList() {
  const [vpcs, setVpcs] = useState([]);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const toast = useToast();

  const fetchVPCs = async () => {
    try {
      const response = await fetch('http://localhost:5000/api/vpc/list');
      const data = await response.json();
      setVpcs(data.vpcs || []);
    } catch (error) {
      toast({
        title: 'Error fetching VPCs',
        description: error.message,
        status: 'error',
        duration: 5000,
        isClosable: true,
      });
    }
  };

  const handleDelete = async (vpcName) => {
    try {
      const response = await fetch(`http://localhost:5000/api/vpc/${vpcName}`, {
        method: 'DELETE',
      });
      if (response.ok) {
        toast({
          title: 'VPC deleted',
          description: `Successfully deleted VPC ${vpcName}`,
          status: 'success',
          duration: 3000,
          isClosable: true,
        });
        fetchVPCs();
      }
    } catch (error) {
      toast({
        title: 'Error deleting VPC',
        description: error.message,
        status: 'error',
        duration: 5000,
        isClosable: true,
      });
    }
  };

  useEffect(() => {
    fetchVPCs();
  }, []);

  return (
    <Box width="100%" maxW="1200px" mx="auto" p={4}>
      <HStack justify="space-between" mb={6}>
        <Heading size="lg">Virtual Private Clouds</Heading>
        <Button colorScheme="blue" onClick={() => setIsCreateModalOpen(true)}>
          Create VPC
        </Button>
      </HStack>

      <Table variant="simple">
        <Thead>
          <Tr>
            <Th>Name</Th>
            <Th>CIDR</Th>
            <Th>Used IPs</Th>
            <Th>Actions</Th>
          </Tr>
        </Thead>
        <Tbody>
          {vpcs.map((vpc) => (
            <Tr key={vpc.name}>
              <Td>{vpc.name}</Td>
              <Td>{vpc.cidr}</Td>
              <Td>{vpc.used_ips?.length || 0}</Td>
              <Td>
                <Button
                  colorScheme="red"
                  size="sm"
                  onClick={() => handleDelete(vpc.name)}
                >
                  Delete
                </Button>
              </Td>
            </Tr>
          ))}
        </Tbody>
      </Table>

      <CreateVPCModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        onVPCCreated={fetchVPCs}
      />
    </Box>
  );
}

export default VPCList; 