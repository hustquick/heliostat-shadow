from pathlib import Path


def test_ios_device_installer_requires_explicit_device_and_team():
    script = (Path(__file__).resolve().parents[1] / "scripts/install_ios_device.sh").read_text()
    assert 'IOS_DEVICE_ID' in script
    assert 'IOS_DEVELOPMENT_TEAM' in script
    assert '-allowProvisioningDeviceRegistration' in script
    assert 'maximum number of installed apps using a free developer profile' in script
    assert 'device install app' in script
    assert 'process launch' in script
