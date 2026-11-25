--- node_modules/@vscode/os-proxy-resolver/index.js.orig
+++ node_modules/@vscode/os-proxy-resolver/index.js
@@ -14,6 +14,7 @@
 	'win32-x64': '@vscode/os-proxy-resolver-win32-x64-msvc',
 	'freebsd-arm64': '@vscode/os-proxy-resolver-freebsd-arm64',
 	'freebsd-x64': '@vscode/os-proxy-resolver-freebsd-x64',
+	'dragonfly-x64': '@vscode/os-proxy-resolver-freebsd-x64',
 };
 
 const platform = getPlatformPackage();
