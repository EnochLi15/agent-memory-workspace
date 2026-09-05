import pathlib,re,shutil,sys
run=sys.argv[1] if len(sys.argv)>1 else ''
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{1,100}',run):raise SystemExit('An explicit safe RUN_ID is required')
root=pathlib.Path(__file__).resolve().parents[1]/'eval'/'artifacts'
target=root/run
if target.is_symlink():raise SystemExit('Refusing symlink')
if target.exists():shutil.rmtree(target)
print('Removed experiment artifacts only:',target)
