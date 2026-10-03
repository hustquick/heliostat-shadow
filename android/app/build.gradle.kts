plugins { id("com.android.application") }

android {
    namespace = "com.hustquick.heliostatviewer"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.hustquick.heliostatviewer"
        minSdk = 26
        targetSdk = 35
        versionCode = 20001
        versionName = "1.0.0"
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    androidResources { noCompress += "gz" }
}
