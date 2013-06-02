--- org/gudy/azureus2/core3/util/Constants.java.orig	2007-01-22 18:58:08.000000000 +0000
+++ org/gudy/azureus2/core3/util/Constants.java
@@ -76,7 +76,7 @@ Constants
   public static final boolean isOSX				= OSName.toLowerCase().startsWith("mac os");
   public static final boolean isLinux			= OSName.equalsIgnoreCase("Linux");
   public static final boolean isSolaris			= OSName.equalsIgnoreCase("SunOS");
-  public static final boolean isFreeBSD			= OSName.equalsIgnoreCase("FreeBSD");
+  public static final boolean isFreeBSD			= OSName.equalsIgnoreCase("FreeBSD") || OSName.equalsIgnoreCase("DragonFly");
   public static final boolean isWindowsXP		= OSName.equalsIgnoreCase("Windows XP");
   public static final boolean isWindows95		= OSName.equalsIgnoreCase("Windows 95");
   public static final boolean isWindows98		= OSName.equalsIgnoreCase("Windows 98");
