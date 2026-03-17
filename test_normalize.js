const normalizeFilePath = (rawPath) => {
  let path = rawPath;
  const prefixPatterns = [
    /^\/[^/]+\/[^/]+\/Documents\/GitHub\/[^/]+\//,
    /^\/[^/]+\/[^/]+\/repos\/[^/]+\//,
    /^\/tmp\/[^/]+\//,
    /^\/var\/[^/]+\/[^/]+\//,
    /^[A-Za-z]:\\[^\\]+\\[^\\]+\\repos\\[^\\]+\\/,
    /^\/var\/folders\/[^/]+\/[^/]+\/T\/[^/]+\//,
    /\/T\/[^/]+\//,
  ];
  for (const pattern of prefixPatterns) {
    path = path.replace(pattern, '');
  }
  path = path.replace(/\\/g, '/');
  if (path.startsWith('./')) path = path.slice(2);
  if (path.startsWith('/')) path = path.slice(1);
  return path;
};

console.log(normalizeFilePath("/var/folders/xx/yy/T/gl6ngq4x28zbyff50_kfm82c0000gn/docker/RepoMapper/repomap_server.py"));
console.log(normalizeFilePath("/tmp/gl6ngq4x28zbyff50_kfm82c0000gn/docker/RepoMapper/repomap_server.py"));
console.log(normalizeFilePath("/var/app/volumes/gl6ngq4x28zbyff50_kfm82c0000gn/docker/RepoMapper/repomap_server.py"));
console.log(normalizeFilePath("/workspace/gl6ngq4x28zbyff50_kfm82c0000gn/docker/RepoMapper/repomap_server.py"));
