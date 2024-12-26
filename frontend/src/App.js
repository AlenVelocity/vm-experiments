import React, { useState, useEffect } from 'react';
import {
  ChakraProvider,
  Box,
  VStack,
  Grid,
  theme,
  Tabs,
  TabList,
  TabPanels,
  Tab,
  TabPanel,
  Button,
  useDisclosure,
  Select,
  FormControl,
  FormLabel,
  HStack,
} from '@chakra-ui/react';
import { BrowserRouter as Router } from 'react-router-dom';
import Navbar from './components/Navbar';
import VMList from './components/VMList';
import VPCList from './components/VPCList';
import CreateVMModal from './components/CreateVMModal';

function App() {
  const [refreshVMs, setRefreshVMs] = useState(0);
  const [selectedCluster, setSelectedCluster] = useState('');
  const [clusters, setClusters] = useState([]);
  const { isOpen, onOpen, onClose } = useDisclosure();

  const fetchClusters = async () => {
    try {
      const response = await fetch('http://localhost:5000/api/clusters');
      const data = await response.json();
      setClusters(data.clusters || []);
      if (data.clusters && data.clusters.length > 0 && !selectedCluster) {
        setSelectedCluster(data.clusters[0].name);
      }
    } catch (error) {
      console.error('Error fetching clusters:', error);
    }
  };

  useEffect(() => {
    fetchClusters();
  }, []);

  const handleVMCreated = () => {
    setRefreshVMs(prev => prev + 1);
  };

  return (
    <ChakraProvider theme={theme}>
      <Router>
        <Box textAlign="center" fontSize="xl">
          <Grid minH="100vh" p={3}>
            <VStack spacing={8}>
              <Navbar />
              <Box width="100%" maxW="1200px">
                <Tabs isFitted variant="enclosed">
                  <TabList mb="1em">
                    <Tab>Virtual Machines</Tab>
                    <Tab>Clusters (VPCs)</Tab>
                  </TabList>
                  <TabPanels>
                    <TabPanel>
                      <Box mb={4}>
                        <HStack justify="space-between">
                          <FormControl maxW="300px">
                            <FormLabel>Select Cluster</FormLabel>
                            <Select
                              value={selectedCluster}
                              onChange={(e) => setSelectedCluster(e.target.value)}
                              placeholder="Select cluster"
                            >
                              {clusters.map((cluster) => (
                                <option key={cluster.name} value={cluster.name}>
                                  {cluster.name}
                                </option>
                              ))}
                            </Select>
                          </FormControl>
                          <Button
                            colorScheme="blue"
                            onClick={onOpen}
                            isDisabled={!selectedCluster}
                          >
                            Create Machine
                          </Button>
                        </HStack>
                      </Box>
                      {selectedCluster && <VMList key={refreshVMs} cluster={selectedCluster} />}
                      <CreateVMModal
                        isOpen={isOpen}
                        onClose={onClose}
                        onVMCreated={handleVMCreated}
                      />
                    </TabPanel>
                    <TabPanel>
                      <VPCList onClusterCreated={fetchClusters} />
                    </TabPanel>
                  </TabPanels>
                </Tabs>
              </Box>
            </VStack>
          </Grid>
        </Box>
      </Router>
    </ChakraProvider>
  );
}

export default App; 