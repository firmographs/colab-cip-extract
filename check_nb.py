import json
with open('CIP_Extraction.ipynb') as f:
    nb = json.load(f)
cells = nb['cells']
print(f"Cells: {len(cells)}")
for c in cells:
    ct = c['cell_type']
    if ct == 'code':
        title = c['source'][0].strip().replace('#@title ', '')[:60]
        nlines = len(c['source'])
        cv = c['metadata'].get('cellView', '?')
        print(f"  CODE [{cv}]  {title}  ({nlines} source lines)")
    else:
        preview = (c['source'][0] if c['source'] else '')[:70].replace('\n', ' ')
        print(f"  MD   {preview}")
