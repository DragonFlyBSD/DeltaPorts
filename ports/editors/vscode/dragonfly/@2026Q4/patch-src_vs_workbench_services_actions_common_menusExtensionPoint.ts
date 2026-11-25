--- src/vs/workbench/services/actions/common/menusExtensionPoint.ts.orig
+++ src/vs/workbench/services/actions/common/menusExtensionPoint.ts
@@ -1280,6 +1280,7 @@
 			case 'win32': key = rawKeyBinding.win; break;
 			case 'linux':
 			case 'freebsd':
+			case 'dragonfly':
 				key = rawKeyBinding.linux;
 				break;
 			case 'darwin': key = rawKeyBinding.mac; break;
