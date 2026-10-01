-- Gallery fingerprints for duplicate matching: dHash (64-bit hex) of up to
-- the first 6 photos of a listing. Two agencies' listings of one flat share
-- photos; look-alike flats in one building do not.
ALTER TABLE property_sources ADD COLUMN photo_phashes TEXT;  -- JSON array of hex strings
