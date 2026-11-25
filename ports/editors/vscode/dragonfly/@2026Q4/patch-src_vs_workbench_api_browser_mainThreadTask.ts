--- src/vs/workbench/api/browser/mainThreadTask.ts.orig
+++ src/vs/workbench/api/browser/mainThreadTask.ts
@@ -728,6 +728,7 @@
 				break;
 			case 'linux':
 			case 'freebsd':
+			case 'dragonfly':
 				platform = Platform.Platform.Linux;
 				break;
 			default:
