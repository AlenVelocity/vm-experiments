import React, { useState } from 'react';
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
} from '@chakra-ui/react';
import { BrowserRouter as Router } from 'react-router-dom';
import Navbar from './components/Navbar';
import VMList from './components/VMList';
import VPCList from './components/VPCList';
import CreateVMModal from './components/CreateVMModal';

function App() {
  const [refreshVMs, setRefreshVMs] = useState(0);
  const { isOpen, onOpen, onClose } = useDisclosure();

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
                    <Tab>Virtual Private Clouds</Tab>
                  </TabList>
                  <TabPanels>
                    <TabPanel>
                      <Box mb={4} textAlign="right">
                        <Button colorScheme="blue" onClick={onOpen}>
                          Create VM
                        </Button>
                      </Box>
                      <VMList key={refreshVMs} />
                      <CreateVMModal
                        isOpen={isOpen}
                        onClose={onClose}
                        onVMCreated={handleVMCreated}
                      />
                    </TabPanel>
                    <TabPanel>
                      <VPCList />
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