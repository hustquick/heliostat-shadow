plugins { id("com.android.application") }

val appVersion = rootProject.file("../VERSION").readText().trim()
val appBuild = rootProject.file("../BUILD_NUMBER").readText().trim().toInt()

android {
    namespace = "com.hustquick.heliostatviewer"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.hustquick.heliostatviewer"
        minSdk = 26
        targetSdk = 35
        versionCode = appBuild
        versionName = appVersion
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
