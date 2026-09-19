plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.chaquo.python")
}

// Copy only the portable package, never the repository root or local credentials.
val scannerSources = tasks.register<Sync>("prepareScannerSources") {
    from(rootProject.file("../../miner_scanner")) {
        include("**/*.py", "profiles/*.json")
        into("miner_scanner")
    }
    into(layout.buildDirectory.dir("generated/scannerPython"))
}
android {
    namespace = "io.github.drubic8.asicmonitor"
    compileSdk = 36
    defaultConfig {
        applicationId = "io.github.drubic8.asicmonitor"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0-alpha.1"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true; buildConfig = true }
}
chaquopy {
    defaultConfig {
        version = "3.13"
        pip { install("-r", "requirements.txt") }
    }
    sourceSets.getByName("main") {
        srcDir(layout.buildDirectory.dir("generated/scannerPython"))
    }
}
tasks.configureEach {
    if (name.endsWith("PythonSources")) dependsOn(scannerSources)
}
dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2025.10.01")
    implementation(composeBom)
    androidTestImplementation(composeBom)
    implementation("androidx.activity:activity-compose:1.11.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.9.4")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.4")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test:runner:1.7.0")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
