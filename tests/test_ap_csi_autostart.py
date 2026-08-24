from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "nexmon_firmware"
    / "csi"
    / "ap-csi-autostart.sh"
)


def test_loaded_candidate_health_path_does_not_issue_live_driver_ioctls():
    source = SCRIPT.read_text()
    loaded_branch = source.split(
        'if [ "$current_hash" = "$EXPECTED_CANDIDATE" ]; then', 1
    )[1].split('[ "$current_hash" = "$EXPECTED_STOCK" ]', 1)[0]

    forbidden = ('"$WL"', '"$NEXUTIL"', "configure_csi", "enforce_wireless_identity")
    assert not any(command in loaded_branch for command in forbidden)


def test_driver_install_quiesces_configured_wireless_helpers():
    source = SCRIPT.read_text()

    assert "AP_CSI_QUIESCE_PROCESSES" in source
    assert "quiesce_wireless_helpers" in source

    install_branch = source.split('log "candidate_install_started=1"', 1)[1]
    assert install_branch.count("quiesce_wireless_helpers") >= 2


def test_loaded_candidate_keeps_late_wireless_helpers_suppressed():
    source = SCRIPT.read_text()
    loaded_branch = source.split(
        'if [ "$current_hash" = "$EXPECTED_CANDIDATE" ]; then', 1
    )[1].split('[ "$current_hash" = "$EXPECTED_STOCK" ]', 1)[0]

    assert "start_quiesce_supervisor" in loaded_branch
    assert 'AP_CSI_QUIESCE_INTERVAL_SECONDS' in source
    assert 'while :' in source
