import struct
d=open('wenquanyi_9pt.pcf','rb').read()
toc={}
for i in range(struct.unpack_from('<I',d,4)[0]):
    t=struct.unpack_from('<IIII',d,8+i*16)
    toc[t[0]]=(t[1],t[2],t[3])
ef,es,eo=toc[32]
enc_start=eo+16
mf,ms,mo=toc[4]
md=d[mo+4:]
for cp,name in [(0x3000,'IDEO_SP'),(0x3001,'IDEO_COMMA'),(0x3042,'HIRA_A'),(0x30A2,'KATA_A'),(0x4E00,'CJK_1'),(0xAC00,'HANGUL'),(0xFF01,'FW_EXCL')]:
    gidx=struct.unpack_from('>h',d,enc_start+cp*2)[0]
    e=md[gidx*5:gidx*5+5]
    w=e[0]-0x80
    print(f'U+{cp:04X} ({name}) gidx={gidx} W={w}')
