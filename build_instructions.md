# Build Instructions for K Download Manager

## Option 1: Use GitHub Actions (Recommended)

1. **Upload to GitHub:**
   - Create new repository on GitHub
   - Upload all files (main_kivy.py, buildozer.spec, main.py)

2. **Setup GitHub Actions:**
   Create `.github/workflows/build.yml`:

```yaml
name: Build APK
on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v3
    
    - name: Setup Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.9'
    
    - name: Install dependencies
      run: |
        sudo apt update
        sudo apt install -y git zip unzip openjdk-8-jdk python3-pip autoconf libtool pkg-config zlib1g-dev libncurses5-dev libncursesw5-dev libtinfo5 cmake libffi-dev libssl-dev
        pip3 install --upgrade buildozer cython==0.29.33
    
    - name: Build APK
      run: |
        buildozer android debug
    
    - name: Upload APK
      uses: actions/upload-artifact@v3
      with:
        name: K-Download-Manager-APK
        path: bin/*.apk
```

## Option 2: Use Online Build Services

### Buildozer.space
1. Go to https://buildozer.space/
2. Upload your project files
3. Click "Build APK"
4. Download the generated APK

### Replit
1. Create new Python repl on replit.com
2. Upload files
3. Install buildozer in shell
4. Run build command

## Option 3: Linux/WSL (Windows Subsystem for Linux)

```bash
# Install WSL on Windows
wsl --install

# In WSL terminal:
sudo apt update
sudo apt install -y git zip unzip openjdk-8-jdk python3-pip autoconf libtool pkg-config zlib1g-dev libncurses5-dev libncursesw5-dev libtinfo5 cmake libffi-dev libssl-dev

# Install buildozer
pip3 install buildozer cython==0.29.33

# Build APK
buildozer android debug
```

## Option 4: Docker Build

Create `Dockerfile`:
```dockerfile
FROM ubuntu:20.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    git zip unzip openjdk-8-jdk python3-pip autoconf libtool pkg-config \
    zlib1g-dev libncurses5-dev libncursesw5-dev libtinfo5 cmake libffi-dev libssl-dev

RUN pip3 install buildozer cython==0.29.33

WORKDIR /app
COPY . .

CMD ["buildozer", "android", "debug"]
```

Run:
```bash
docker build -t k-download-manager .
docker run -v $(pwd)/bin:/app/bin k-download-manager
```

## Files Needed for Build:
- main_kivy.py (main app file)
- main.py (entry point)
- buildozer.spec (build configuration)
- README.md (documentation)

## Expected Output:
- APK file in `bin/` folder
- Size: ~15-25 MB
- Compatible with Android 5.0+ (API 21+)

## Troubleshooting:
- If build fails, check buildozer.spec requirements
- Ensure all Python dependencies are listed
- Use stable versions of kivy/kivymd