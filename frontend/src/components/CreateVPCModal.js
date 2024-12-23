import React, { useState } from 'react';
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
  useToast,
} from '@chakra-ui/react';

function CreateVPCModal({ isOpen, onClose, onVPCCreated }) {
  const [name, setName] = useState('');
  const [cidr, setCidr] = useState('192.168.0.0/16');
  const [isLoading, setIsLoading] = useState(false);
  const toast = useToast();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setIsLoading(true);

    try {
      const response = await fetch('http://localhost:5000/api/clusters', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name, cidr }),
      });

      if (response.ok) {
        toast({
          title: 'Cluster created',
          description: `Successfully created cluster ${name}`,
          status: 'success',
          duration: 3000,
          isClosable: true,
        });
        onVPCCreated();
        onClose();
        setName('');
        setCidr('192.168.0.0/16');
      } else {
        const error = await response.json();
        throw new Error(error.message || 'Failed to create cluster');
      }
    } catch (error) {
      toast({
        title: 'Error creating cluster',
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
          <ModalHeader>Create New Cluster</ModalHeader>
          <ModalCloseButton />
          <ModalBody>
            <FormControl isRequired>
              <FormLabel>Cluster Name</FormLabel>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="my-cluster"
              />
            </FormControl>
            <FormControl mt={4}>
              <FormLabel>CIDR Block</FormLabel>
              <Input
                value={cidr}
                onChange={(e) => setCidr(e.target.value)}
                placeholder="192.168.0.0/16"
              />
            </FormControl>
          </ModalBody>
          <ModalFooter>
            <Button
              colorScheme="blue"
              mr={3}
              type="submit"
              isLoading={isLoading}
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

export default CreateVPCModal; 