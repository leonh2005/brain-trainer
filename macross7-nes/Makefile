NAME    = macross7
CC      = cl65
CFLAGS  = -t nes --include-dir src -O
LDFLAGS = -t nes -C nes.cfg

SRCS    = src/main.c src/player.c src/enemy.c src/bullet.c src/song.c src/scroll.c src/hud.c
ASMS    = src/crt0.s

TARGET  = build/$(NAME).nes

all: $(TARGET)

$(TARGET): $(SRCS) $(ASMS) src/chr_data.s
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $(ASMS) $(SRCS) src/chr_data.s

clean:
	rm -f build/*.nes build/*.o build/*.s

.PHONY: all clean
