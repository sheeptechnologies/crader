#!/bin/bash
set -e

# Data definition
SCIP_VERSION="v0.4.0" # Sticking to a known stable version or latest v0.6.1 if desired, but v0.4.0 is very common. Let's use 0.4.0 for broader compatibility unless specified. Actually user has 0.6.1. Let's aim for 0.6.1
SCIP_VERSION="v0.6.1"
INSTALL_DIR="/usr/local/bin"
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "\n${BLUE}=========================================="
echo -e "   🐑 Crader (Sheep Codebase Indexer) Setup"
echo -e "==========================================${NC}\n"

# --- Python Environment Setup ---

REQUIRED_PYTHON_MAX_MINOR=11
REQUIRED_PYTHON_MIN_MINOR=9

get_python_minor_version() {
    $1 -c "import sys; print(sys.version_info.minor)"
}

find_compatible_python() {
    # Check default python3
    if command -v python3 &> /dev/null; then
        ver=$(get_python_minor_version python3)
        if [ "$ver" -le "$REQUIRED_PYTHON_MAX_MINOR" ] && [ "$ver" -ge "$REQUIRED_PYTHON_MIN_MINOR" ]; then
            echo "python3"
            return
        fi
    fi

    # Check specific versions
    for ver in 11 10 9; do
        if command -v "python3.$ver" &> /dev/null; then
            echo "python3.$ver"
            return
        fi
    done
}

TARGET_PYTHON=$(find_compatible_python)

if [ -z "$TARGET_PYTHON" ]; then
    echo -e "${RED}❌ No compatible Python version found (3.$REQUIRED_PYTHON_MIN_MINOR - 3.$REQUIRED_PYTHON_MAX_MINOR required).${NC}"
    echo -e "   Current python3 is: $(python3 --version 2>&1)"
    echo -e "   Please install Python 3.11 (e.g., 'brew install python@3.11')."
    exit 1
fi

echo -e "   🔍 Selected Python interpreter: ${TARGET_PYTHON} ($( $TARGET_PYTHON --version ))"

echo -e "\n${GREEN}🔍 Checking system dependencies...${NC}"

# Check/Install SCIP
if ! command -v scip &> /dev/null; then
    echo -e "   ${BLUE}⚙️  SCIP not found. Installing scip ${SCIP_VERSION}...${NC}"
    
    OS="$(uname -s)"
    ARCH="$(uname -m)"
    
    if [ "$OS" = "Darwin" ]; then
        PLATFORM="macosx"
    elif [ "$OS" = "Linux" ]; then
        PLATFORM="linux"
    else
        echo -e "${RED}❌ Unsupported OS: $OS${NC}"
        exit 1
    fi

    if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
        ARCH="aarch64"
    else
        ARCH="x86_64"
    fi

    DOWNLOAD_URL="https://github.com/sourcegraph/scip/releases/download/${SCIP_VERSION}/scip-${PLATFORM}-${ARCH}.tar.gz"
    
    echo -e "   📥 Downloading from: $DOWNLOAD_URL"
    curl -L "$DOWNLOAD_URL" -o scip.tar.gz
    
    echo -e "   📦 Extracting..."
    tar -xzf scip.tar.gz
    
    echo -e "   🔐 Requesting sudo access to move binary to /usr/local/bin..."
    sudo mv scip /usr/local/bin/
    rm scip.tar.gz
    
    echo -e "   ✅ SCIP installed successfully!"
else
    CURRENT_SCIP=$(scip --version)
    echo -e "   ✅ SCIP is already installed: ${CURRENT_SCIP}"
fi

# Check for Virtual Environment
if [[ -z "$VIRTUAL_ENV" ]]; then
    echo -e "${BLUE}ℹ️  No active virtual environment detected.${NC}"
    
    CREATE_NEW_VENV=false
    
    if [ -d ".venv" ]; then
        # Check if existing venv is compatible/healthy
        if [ -f ".venv/bin/python3" ]; then
             VENV_VER=$(get_python_minor_version .venv/bin/python3)
             if [ "$VENV_VER" -gt "$REQUIRED_PYTHON_MAX_MINOR" ] || [ "$VENV_VER" -lt "$REQUIRED_PYTHON_MIN_MINOR" ]; then
                 echo -e "${RED}⚠️  Existing .venv is incompatible (Python 3.${VENV_VER}). Recreating with ${TARGET_PYTHON}...${NC}"
                 rm -rf .venv
                 CREATE_NEW_VENV=true
             else
                 echo -e "   ✅ Found compatible .venv"
             fi
        else
             echo -e "${RED}⚠️  Broken .venv detected. Recreating...${NC}"
             rm -rf .venv
             CREATE_NEW_VENV=true
        fi
    else
        CREATE_NEW_VENV=true
    fi

    if [ "$CREATE_NEW_VENV" = true ]; then
        echo -e "   🔨 Creating new virtual environment with ${TARGET_PYTHON}..."
        $TARGET_PYTHON -m venv .venv
    fi
    
    source .venv/bin/activate
    PROMPT_TO_ACTIVATE=true
else
    echo -e "${GREEN}✅ Running inside active virtual environment: $VIRTUAL_ENV${NC}"
fi

# Upgrade pip just in case
pip install --upgrade pip -q

echo -e "\n${GREEN}🐍 Installing Python dependencies...${NC}"
python3 -m pip install -r requirements.txt

echo -e "\n${BLUE}=========================================="
echo -e "   🚀 Installation Complete!"
if [ "$PROMPT_TO_ACTIVATE" = true ]; then
    echo -e "\n${BLUE}👉 To start using the library, run:${NC}"
    echo -e "   source .venv/bin/activate"
fi
echo -e "\n   Usage: python3 -m code_graph_indexer --help"
echo -e "==========================================${NC}\n"
