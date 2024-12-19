import React from 'react';
import {
  Modal,
  ModalOverlay,
  ModalContent,
  ModalHeader,
  ModalBody,
  ModalCloseButton,
} from '@chakra-ui/react';
import VMConsole from './VMConsole';

function VMConsoleModal({ isOpen, onClose, vmName }) {
  return (
    <Modal isOpen={isOpen} onClose={onClose} size="xl">
      <ModalOverlay />
      <ModalContent maxW="90vw" h="600px">
        <ModalHeader>Console: {vmName}</ModalHeader>
        <ModalCloseButton />
        <ModalBody p={0}>
          <VMConsole vmName={vmName} isOpen={isOpen} />
        </ModalBody>
      </ModalContent>
    </Modal>
  );
}

export default VMConsoleModal; 