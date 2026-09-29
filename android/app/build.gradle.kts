plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.bsd.filter1"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.bsd.filter1"
        minSdk = 24          // Android 7.0
        targetSdk = 34
        versionCode = 1
        versionName = "1.0.0"

        // Default control server; the token is entered in-app (or pushed by MDM).
        buildConfigField("String", "DEFAULT_SERVER", "\"https://bsd-filter1.fly.dev\"")
    }

    buildFeatures {
        buildConfig = true
        viewBinding = false
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
}
