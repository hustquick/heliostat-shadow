package com.hustquick.heliostatviewer;

public final class NativeCore {
    static { System.loadLibrary("_heliostat_rust"); }
    private NativeCore() {}
    public static native String computeFieldJson(String payload);
    public static native String initializeBundle(byte[] payload);
    public static native String requestJson(String payload);

    public static boolean selfTest() {
        String input = "{\"mirrors\":[{\"mirror_id\":\"self-test\",\"centre\":[0,0,0]," +
                "\"width\":2,\"height\":2,\"aim_point\":[0,0,20],\"mount_type\":\"projected_east\"}]," +
                "\"sun\":[0,0,1],\"target_indices\":[0]}";
        try {
            String output = computeFieldJson(input);
            return output.contains("\"eta_joint\":1.0");
        } catch (Throwable error) {
            return false;
        }
    }
}
