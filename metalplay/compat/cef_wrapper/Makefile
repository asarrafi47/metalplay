TARGETS  = SocialClubHelper.exe Launcher.exe
CC      ?= x86_64-w64-mingw32-gcc
CFLAGS  ?= -O2 -Wall -Wextra -municode -DUNICODE -D_UNICODE
LDFLAGS ?= -static -lshell32 -mwindows

.PHONY: all clean

all: $(TARGETS)

SocialClubHelper.exe: src/socialclubhelper-wrapper.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

Launcher.exe: src/launcher-wrapper.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f $(TARGETS)
