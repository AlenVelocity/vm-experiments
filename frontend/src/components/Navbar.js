import React from 'react';
import {
  Box,
  Flex,
  Heading,
} from '@chakra-ui/react';

function Navbar() {
  return (
    <Box w="100%" p={4} bg="gray.100" borderRadius="lg">
      <Flex minWidth="max-content" alignItems="center" gap="2">
        <Box p="2">
          <Heading size="md">VM Manager</Heading>
        </Box>
      </Flex>
    </Box>
  );
}

export default Navbar; 