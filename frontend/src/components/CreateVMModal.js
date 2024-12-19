import React, { useState, useEffect } from 'react';
import {
  Modal,
  ModalOverlay,
  ModalContent,
  ModalHeader,
  ModalFooter,
  ModalBody,
  ModalCloseButton,
  Button,
  FormControl,
  FormLabel,
  Input,
  Select,
  useToast,
} from '@chakra-ui/react';

function CreateVMModal({ isOpen, onClose, onVMCreated }) {
  const [name, setName] = useState('');
  const [vpc, setVpc] = useState('');
  const [vpcs, setVpcs] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const toast = useToast();

  const fetchVPCs = async () => {
    try {
      const response = await fetch('http://localhost:5000/api/vpc/list');
      const data = await response.json();
      setVpcs(data.vpcs || []);
      if (data.vpcs && data.vpcs.length > 0) {
        setVpc(data.vpcs[0].name);
      }
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

  useEffect(() => {
    if (isOpen) {
      fetchVPCs();
    }
  }, [isOpen]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setIsLoading(true);

    try {
      const response = await fetch('http://localhost:5000/api/vms/create', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name, vpc }),
      });

      if (response.ok) {
        toast({
          title: 'VM created',
          description: `Successfully created VM ${name} in VPC ${vpc}`,
          status: 'success',
          duration: 3000,
          isClosable: true,
        });
        onVMCreated();
        onClose();
        setName('');
        setVpc('');
      } else {
        const error = await response.json();
        throw new Error(error.message || 'Failed to create VM');
      }
    } catch (error) {
      toast({
        title: 'Error creating VM',
        description: error.message,
        status: 'error',
        duration: 5000,
        isClosable: true,
      });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose}>
      <ModalOverlay />
      <ModalContent>
        <form onSubmit={handleSubmit}>
          <ModalHeader>Create New VM</ModalHeader>
          <ModalCloseButton />
          <ModalBody>
            <FormControl isRequired>
              <FormLabel>VM Name</FormLabel>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="my-vm"
              />
            </FormControl>
            <FormControl mt={4} isRequired>
              <FormLabel>VPC</FormLabel>
              <Select
                value={vpc}
                onChange={(e) => setVpc(e.target.value)}
                placeholder="Select VPC"
              >
                {vpcs.map((vpc) => (
                  <option key={vpc.name} value={vpc.name}>
                    {vpc.name} ({vpc.cidr})
                  </option>
                ))}
              </Select>
            </FormControl>
          </ModalBody>
          <ModalFooter>
            <Button
              colorScheme="blue"
              mr={3}
              type="submit"
              isLoading={isLoading}
              isDisabled={!vpc}
            >
              Create
            </Button>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
          </ModalFooter>
        </form>
      </ModalContent>
    </Modal>
  );
}

export default CreateVMModal; 