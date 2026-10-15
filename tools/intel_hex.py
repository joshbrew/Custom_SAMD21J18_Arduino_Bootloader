"""Checked Intel HEX parsing/merging. Preserve addresses and reject overlap conflicts."""
from pathlib import Path


def parse(text):
    image={}
    base=0
    ended=False
    for number,line in enumerate(text.splitlines(),1):
        line=line.strip()
        if not line: continue
        if ended or not line.startswith(':'):
            raise ValueError(f'Unexpected HEX record at line {number}')
        record=bytes.fromhex(line[1:])
        if len(record)<5 or len(record)!=record[0]+5 or sum(record)&255:
            raise ValueError(f'HEX length/checksum error at line {number}')
        size,kind=record[0],record[3]
        offset=int.from_bytes(record[1:3],'big')
        data=record[4:-1]
        if kind==0:
            for i,value in enumerate(data):
                address=base+offset+i
                if address in image and image[address]!=value:
                    raise ValueError(f'Conflicting HEX data at {address:#x}')
                image[address]=value
        elif kind==1 and size==0 and offset==0:
            ended=True
        elif kind in (2,4) and size==2 and offset==0:
            base=int.from_bytes(data,'big') << (4 if kind==2 else 16)
        elif kind in (3,5) and size==4 and offset==0:
            pass # Entry metadata; flash vector tables determine reset entry.
        else:
            raise ValueError(f'Unsupported/malformed HEX record at line {number}')
    if not ended or not image:
        raise ValueError('Missing HEX EOF/data.')
    return image


def merge(*images):
    result={}
    for image in images:
        for address,value in image.items():
            if address in result and result[address]!=value:
                raise ValueError(f'Conflicting images at {address:#x}')
            result[address]=value
    return result


def record(kind,offset,data):
    body=bytes([len(data)])+offset.to_bytes(2,'big')+bytes([kind])+bytes(data)
    return ':'+(body+bytes([-sum(body)&255])).hex().upper()+'\n'


def encode(image):
    addresses=sorted(image)
    lines=[]
    upper=None
    i=0
    while i<len(addresses):
        start=addresses[i]
        if start>>16 != upper:
            upper=start>>16
            lines.append(record(4,0,upper.to_bytes(2,'big')))
        data=[]
        while i<len(addresses) and addresses[i]==start+len(data) and addresses[i]>>16==upper and len(data)<16:
            data.append(image[addresses[i]]); i+=1
        lines.append(record(0,start&65535,data))
    lines.append(record(1,0,b''))
    return ''.join(lines)


def checked_dual_core(app,net):
    def vector(image,base,ram_low,ram_high,flash_high):
        try: values=bytes(image[base+i] for i in range(8))
        except KeyError: raise ValueError(f'Missing core vectors at {base:#x}') from None
        sp=int.from_bytes(values[:4],'little'); pc=int.from_bytes(values[4:],'little')
        if not ram_low<sp<=ram_high or not pc&1 or not base<=(pc&~1)<flash_high:
            raise ValueError(f'Invalid core vectors at {base:#x}')
    vector(app,0,0x20000000,0x20080000,0x100000)
    vector(net,0x01000000,0x21000000,0x21040000,0x01040000)
    return merge(app,net)
