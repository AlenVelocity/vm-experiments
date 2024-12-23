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
  const [cluster, setCluster] = useState('');
  const [clusters, setClusters] = useState([]);
  const [machineType, setMachineType] = useState('');
  const [machineTypes, setMachineTypes] = useState([]);
  const [image, setImage] = useState('');
  const [images, setImages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const toast = useToast();

  const fetchClusters = async () => {
    try {
      const response = await fetch('http://localhost:5000/api/clusters');
      const data = await response.json();
      setClusters(data.clusters || []);
      if (data.clusters && data.clusters.length > 0) {
        setCluster(data.clusters[0].name);
      }
    } catch (error) {
      toast({
        title: 'Error fetching clusters',
        description: error.message,
        status: 'error',
        duration: 5000,
        isClosable: true,
      });
    }
  };

  const fetchMachineTypes = async () => {
    try {
      const response = await fetch('http://localhost:5000/api/machine-types');
      const data = await response.json();
      setMachineTypes(data.machine_types || []);
      if (data.machine_types && data.machine_types.length > 0) {
        setMachineType(data.machine_types[0].id);
      }
    } catch (error) {
      toast({
        title: 'Error fetching machine types',
        description: error.message,
        status: 'error',
        duration: 5000,
        isClosable: true,
      });
    }
  };

  const fetchImages = async () => {
    try {
      const response = await fetch('http://localhost:5000/api/images');
      const data = await response.json();
      setImages(data.images || []);
      if (data.images && data.images.length > 0) {
        setImage(data.images[0].id);
      }
    } catch (error) {
      toast({
        title: 'Error fetching images',
        description: error.message,
        status: 'error',
        duration: 5000,
        isClosable: true,
      });
    }
  };

  useEffect(() => {
    if (isOpen) {
      fetchClusters();
      fetchMachineTypes();
      fetchImages();
    }
  }, [isOpen]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setIsLoading(true);

    try {
      const response = await fetch(`http://localhost:5000/api/clusters/${cluster}/machines`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          name,
          machine_type: machineType,
          image,
        }),
      });

      if (response.ok) {
        toast({
          title: 'Machine created',
          description: `Successfully created machine ${name} in cluster ${cluster}`,
          status: 'success',
          duration: 3000,
          isClosable: true,
        });
        onVMCreated();
        onClose();
        setName('');
        setCluster('');
        setMachineType('');
        setImage('');
      } else {
        const error = await response.json();
        throw new Error(error.message || 'Failed to create machine');
      }
    } catch (error) {
      toast({
        title: 'Error creating machine',
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
          <ModalHeader>Create New Machine</ModalHeader>
          <ModalCloseButton />
          <ModalBody>
            <FormControl isRequired>
              <FormLabel>Machine Name</FormLabel>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="my-machine"
              />
            </FormControl>
            <FormControl mt={4} isRequired>
              <FormLabel>Cluster</FormLabel>
              <Select
                value={cluster}
                onChange={(e) => setCluster(e.target.value)}
                placeholder="Select cluster"
              >
                {clusters.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name} ({c.cidr})
                  </option>
                ))}
              </Select>
            </FormControl>
            <FormControl mt={4} isRequired>
              <FormLabel>Machine Type</FormLabel>
              <Select
                value={machineType}
                onChange={(e) => setMachineType(e.target.value)}
                placeholder="Select machine type"
              >
                {machineTypes.map((type) => (
                  <option key={type.id} value={type.id}>
                    {type.name} ({type.cpu_cores} CPU, {type.memory_mb}MB RAM)
                  </option>
                ))}
              </Select>
            </FormControl>
            <FormControl mt={4} isRequired>
              <FormLabel>Image</FormLabel>
              <Select
                value={image}
                onChange={(e) => setImage(e.target.value)}
                placeholder="Select image"
              >
                {images.map((img) => (
                  <option key={img.id} value={img.id}>
                    {img.name} ({img.version})
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
              isDisabled={!cluster || !machineType || !image}
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