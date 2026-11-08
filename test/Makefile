# Host tests (needs gcc/g++, python3, py65; the Apple side must be built)
#
#   make -C test          build and run everything
#   mingw32-make -C test  the same on Windows (e.g. WinLibs MinGW-w64)

FW = ../src
PY = python3

ifeq ($(OS),Windows_NT)
# .exe, and the gcc runtime linked in: Python does not search PATH for the
# DLLs a library needs. The test is started by its full path and clean uses
# Python, so the recipes work with cmd.exe as well as with sh.
EXE     = .exe
SOFLAGS = -static-libgcc -static-libstdc++ -static
RM      = $(PY) -c "import os, sys; [os.remove(f) for f in sys.argv[1:] if os.path.exists(f)]"
else
EXE     =
SOFLAGS = -fPIC
RM      = rm -f
endif

TEST_PROTO = test_proto$(EXE)

all: run

$(TEST_PROTO): test_proto.c $(FW)/sp_proto.c $(FW)/sp_proto.h
	gcc -Wall -Wextra -O2 -o $@ test_proto.c $(FW)/sp_proto.c

libspiisd.so: host_sim.cpp host/SdFat.h host/Arduino.h $(FW)/main.cpp $(FW)/sp_proto.c $(FW)/bootimg.h
	g++ -Wall -Wno-int-to-pointer-cast -O1 -g -shared $(SOFLAGS) -Ihost -I$(FW) -o $@ host_sim.cpp -x c $(FW)/sp_proto.c

run: $(TEST_PROTO) libspiisd.so
	$(CURDIR)/$(TEST_PROTO)
	$(PY) sim_e2e.py

clean:
	$(RM) $(TEST_PROTO) libspiisd.so

.PHONY: all run clean
