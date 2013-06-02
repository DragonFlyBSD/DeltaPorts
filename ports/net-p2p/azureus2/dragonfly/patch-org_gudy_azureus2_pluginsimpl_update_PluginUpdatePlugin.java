--- org/gudy/azureus2/pluginsimpl/update/PluginUpdatePlugin.java.orig	2007-01-20 00:31:36.000000000 +0000
+++ org/gudy/azureus2/pluginsimpl/update/PluginUpdatePlugin.java
@@ -878,6 +878,7 @@ PluginUpdatePlugin
 												(Constants.isLinux && platform.equalsIgnoreCase( "linux" ))	||
 												(Constants.isUnix && platform.equalsIgnoreCase( "unix" ))	||
 												(Constants.isFreeBSD && platform.equalsIgnoreCase( "freebsd" ))	||
+												(Constants.isFreeBSD && platform.equalsIgnoreCase( "dragonfly" ))	||
 												(Constants.isSolaris && platform.equalsIgnoreCase( "solaris" ))	||
 												(Constants.isOSX && platform.equalsIgnoreCase( "osx" ))){
 											
