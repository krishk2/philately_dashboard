import pytest
from PIL import Image, ImageEnhance

from app.cli import draw_stamp
from app.verification.imagehash import dhash, hamming_distance, similarity


def test_identical_images_have_identical_hashes():
    assert dhash(draw_stamp(1, "A", "1")) == dhash(draw_stamp(1, "A", "1"))


def test_hash_is_16_hex_chars():
    value = dhash(draw_stamp(1, "A", "1"))
    assert len(value) == 16
    int(value, 16)  # parses as hex


def test_resized_and_recompressed_copy_stays_close(tmp_path):
    original = draw_stamp(5, "Lepakshi", "Rs 5")
    path = tmp_path / "copy.jpg"
    original.resize((150, 180)).save(path, format="JPEG", quality=60)
    assert hamming_distance(dhash(original), dhash(path)) <= 6


def test_brightness_change_stays_close():
    original = draw_stamp(5, "Lepakshi", "Rs 5")
    brighter = ImageEnhance.Brightness(original).enhance(1.2)
    assert hamming_distance(dhash(original), dhash(brighter)) <= 6


def test_different_stamps_are_far_apart():
    distances = [
        hamming_distance(dhash(draw_stamp(a, "X", "1")), dhash(draw_stamp(b, "Y", "2")))
        for a, b in [(1, 2), (3, 4), (10, 99), (42, 7)]
    ]
    assert min(distances) > 18  # beyond the "needs review" threshold


def test_shared_border_does_not_make_stamps_look_alike():
    # Regression: before cropping, different stamps with the same perforated border
    # could be as close as 3 bits apart and get flagged as duplicates.
    import itertools

    hashes = [dhash(draw_stamp(seed, "X", "1")) for seed in range(1, 25)]
    closest = min(hamming_distance(a, b) for a, b in itertools.combinations(hashes, 2))
    assert closest > 6  # the duplicate-photo threshold


def test_hamming_distance_counts_bits():
    assert hamming_distance("0000000000000000", "0000000000000000") == 0
    assert hamming_distance("0000000000000000", "000000000000000f") == 4
    assert hamming_distance("ffffffffffffffff", "0000000000000000") == 64


def test_hamming_distance_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        hamming_distance("00", "0000")


def test_similarity_range():
    assert similarity("ffffffffffffffff", "ffffffffffffffff") == 1
    assert similarity("ffffffffffffffff", "0000000000000000") == 0


def test_accepts_file_paths(tmp_path):
    path = tmp_path / "s.png"
    image = Image.new("RGB", (50, 50), "red")
    image.save(path)
    assert dhash(path) == dhash(image)
