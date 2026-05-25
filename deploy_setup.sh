#!/bin/bash

# ==============================================================================
# ScannerPro Automated Deployment Setup Script for Ubuntu 22.04 LTS
# ==============================================================================
# This script automates the installation of Docker and Docker Compose,
# configures user permissions, and prepares the environment for running the 
# ScannerPro containerized trading system on a Google Cloud VM.
# ==============================================================================

# Exit immediately if a command exits with a non-zero status
set -e

# Define terminal colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}====================================================${NC}"
echo -e "${GREEN}      Starting ScannerPro GCP Server Setup script    ${NC}"
echo -e "${BLUE}====================================================${NC}"

# Check if script is run as root
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Error: Please run this script with sudo:${NC}"
  echo -e "sudo bash deploy_setup.sh"
  exit 1
fi

echo -e "\n${YELLOW}[Step 1/4] Updating packages and installing prerequisites...${NC}"
apt-get update -y
apt-get install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git \
    ufw

echo -e "\n${YELLOW}[Step 2/4] Installing Docker and Docker Compose...${NC}"
# Remove older Docker versions if any exist
apt-get remove -y docker docker-engine docker.io containerd runc || true

# Add Docker's official GPG key
mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes

# Set up the stable repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine & Compose plugin
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Enable and start Docker service
systemctl enable docker
systemctl start docker

echo -e "${GREEN}✓ Docker & Docker Compose installed successfully!${NC}"

echo -e "\n${YELLOW}[Step 3/4] Configuring user permissions for Docker...${NC}"
# Add standard GCP VM user (and the invoking sudo user) to the docker group
# This allows running docker commands without typing sudo
SUDO_USER_NAME=${SUDO_USER:-$USER}
if [ -n "$SUDO_USER_NAME" ] && [ "$SUDO_USER_NAME" != "root" ]; then
    usermod -aG docker "$SUDO_USER_NAME"
    echo -e "${GREEN}✓ Added user '${SUDO_USER_NAME}' to the docker group.${NC}"
else
    echo -e "${YELLOW}No standard user detected, skipping group assignment.${NC}"
fi

echo -e "\n${YELLOW}[Step 4/4] Setting up VM timezone to India (IST)...${NC}"
# Trading is based on Indian markets, syncing the host VM time keeps logs and alerts aligned.
timedatectl set-timezone Asia/Kolkata
echo -e "${GREEN}✓ VM timezone set to Asia/Kolkata (IST). Current VM Time: $(date)${NC}"

echo -e "\n${BLUE}====================================================${NC}"
echo -e "${GREEN}            Setup Completed Successfully!            ${NC}"
echo -e "${BLUE}====================================================${NC}"
echo -e "\n${YELLOW}NEXT STEPS TO LAUNCH SCANNERPRO:${NC}"
echo -e "1. Log out of your SSH session and log back in (so group changes take effect)."
echo -e "2. Navigate to your project directory containing the code files."
echo -e "3. Ensure your actual '${GREEN}config.py${NC}' file containing Zerodha & Telegram credentials is in the directory."
echo -e "4. Build and launch the application in the background by running:"
echo -e "   ${GREEN}docker compose up --build -d${NC}"
echo -e "5. View active services and their status:"
echo -e "   ${GREEN}docker compose ps${NC}"
echo -e "6. To monitor active background scanner logs:"
echo -e "   ${GREEN}docker compose logs -f scheduler${NC}"
echo -e "7. To shut down the services safe and sound:"
echo -e "   ${GREEN}docker compose down${NC}"
echo -e "${BLUE}====================================================${NC}"
