const axios = require('axios');

const API_BASE_URL = 'http://localhost:5000/api';

// Test configuration
const TEST_CONFIG = {
    vpc: {
        name: 'test-vpc'
    },
    vm: {
        name: 'test-vm',
        network_name: 'test-vpc',
        cpu_cores: 1,
        memory_mb: 1024,
        disk_size_gb: 10,
        image_id: 'ubuntu-20.04' // This should match an available image
    }
};

// Helper function to make API calls
async function apiCall(method, endpoint, data = null) {
    try {
        const config = {
            method,
            url: `${API_BASE_URL}${endpoint}`,
            headers: {
                'Content-Type': 'application/json'
            }
        };
        
        if (data) {
            config.data = data;
        }
        
        const response = await axios(config);
        return response.data;
    } catch (error) {
        console.error(`Error calling ${endpoint}:`, error.response?.data || error.message);
        throw error;
    }
}

// Test functions
async function testHealthCheck() {
    console.log('\n🏥 Testing Health Check...');
    const result = await apiCall('GET', '/health');
    console.log('Health Status:', result.status);
    return result;
}

async function testListImages() {
    console.log('\n📸 Testing List Images...');
    const result = await apiCall('GET', '/images');
    console.log('Available Images:', result.images);
    return result;
}

async function testVPCOperations() {
    console.log('\n🌐 Testing VPC Operations...');
    
    // Create VPC
    console.log('Creating VPC...');
    const createResult = await apiCall('POST', '/vpcs', TEST_CONFIG.vpc);
    console.log('VPC Created:', createResult.vpc);
    
    // List VPCs
    console.log('Listing VPCs...');
    const listResult = await apiCall('GET', '/vpcs');
    console.log('VPCs:', listResult.vpcs);
    
    // Get VPC
    console.log('Getting VPC Details...');
    const getResult = await apiCall('GET', `/vpcs/${TEST_CONFIG.vpc.name}`);
    console.log('VPC Details:', getResult.vpc);
    
    return { createResult, listResult, getResult };
}

async function testVMOperations() {
    console.log('\n💻 Testing VM Operations...');
    
    // Create VM
    console.log('Creating VM...');
    const createResult = await apiCall('POST', '/vms', TEST_CONFIG.vm);
    console.log('VM Created:', createResult.vm);
    
    // List VMs
    console.log('Listing VMs...');
    const listResult = await apiCall('GET', '/vms');
    console.log('VMs:', listResult.vms);
    
    // Get VM Status
    console.log('Getting VM Status...');
    const statusResult = await apiCall('GET', `/vms/${TEST_CONFIG.vm.name}/status`);
    console.log('VM Status:', statusResult.status);
    
    // Get VM Metrics
    console.log('Getting VM Metrics...');
    const metricsResult = await apiCall('GET', `/vms/${TEST_CONFIG.vm.name}/metrics`);
    console.log('VM Metrics:', metricsResult.metrics);
    
    return { createResult, listResult, statusResult, metricsResult };
}

async function testDiskOperations() {
    console.log('\n💾 Testing Disk Operations...');
    
    // List Disks
    console.log('Listing Disks...');
    const listResult = await apiCall('GET', '/disks');
    console.log('Disks:', listResult.disks);
    
    // Create Disk
    console.log('Creating Disk...');
    const createResult = await apiCall('POST', '/disks/create', {
        name: 'test-disk',
        size_gb: 5
    });
    console.log('Disk Created:', createResult.disk);
    
    return { listResult, createResult };
}

async function cleanup() {
    console.log('\n🧹 Cleaning up...');
    
    try {
        // Delete VM
        console.log('Deleting VM...');
        await apiCall('DELETE', `/vms/${TEST_CONFIG.vm.name}`);
        console.log('VM deleted successfully');
        
        // Delete VPC
        console.log('Deleting VPC...');
        await apiCall('DELETE', `/vpcs/${TEST_CONFIG.vpc.name}`);
        console.log('VPC deleted successfully');
    } catch (error) {
        console.error('Error during cleanup:', error.message);
    }
}

// Main test runner
async function runTests() {
    try {
        console.log('🚀 Starting API Tests...');
        
        // Run tests in sequence
        await testHealthCheck();
        await testListImages();
        const vpcResults = await testVPCOperations();
        const vmResults = await testVMOperations();
        const diskResults = await testDiskOperations();
        
        console.log('\n✅ All tests completed successfully!');
    } catch (error) {
        console.error('\n❌ Test suite failed:', error.message);
    } finally {
        await cleanup();
    }
}

// Run the tests
runTests(); 