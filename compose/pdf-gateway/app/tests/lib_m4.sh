# Shared helpers for local M4/M5 stack tests (docker-compose.m4-gate.yml).
# Callers set: dc, psql, TEST_STORAGE, limits, admin.

# Stage-isolated seeding through the real guards (see test_m4_worker.sh); the
# API's own outbox publisher delivers ARCHIVE_QUEUED to the archive worker.
# seed N FIXTURE [INTENT]; ANALYSIS_IMAGE selects the text-extraction image.
seed() {
  local n=$1 fixture=$2 intent=${3:-ARCHIVE} scan out out_hash src_hash bytes summary
  scan="$(scan_id "$n")"; out="cdr-output/$scan-1.pdf"
  cp "$TEST_STORAGE/fixtures/$fixture" "$TEST_STORAGE/$out"; chmod 0644 "$TEST_STORAGE/$out"
  out_hash="$(sha256sum "$TEST_STORAGE/$out" | cut -d' ' -f1)"; bytes="$(stat -c %s "$TEST_STORAGE/$out")"
  src_hash="$(printf 'original-%s' "$out_hash" | sha256sum | cut -d' ' -f1)"
  summary="$(docker run --rm "${limits[@]}" --user "$(id -u):$(id -g)" -v "$TEST_STORAGE:/data" --entrypoint python3 \
    "${ANALYSIS_IMAGE:-pdf-gateway-m4-test/analysis-worker:local}" /opt/pdf-gateway/extract_text.py "/data/fixtures/$fixture" "/data/text-extracts/$scan.txt")"
  [[ -f $TEST_STORAGE/text-extracts/$scan.txt ]] && chmod 0644 "$TEST_STORAGE/text-extracts/$scan.txt"
  "${psql[@]}" <<SQL
INSERT INTO scan_jobs(id,owner_id,status,intent,cdr_mode,display_name,source_bytes,source_sha256,quarantine_object,detection,policy,artifact_state)
VALUES ('$scan','$admin','VALIDATING_OUTPUT','$intent','RASTER','$fixture',$bytes,'$src_hash','quar-${scan:5}','CLEAN','CDR_ELIGIBLE','SANITIZED_STAGED');
INSERT INTO job_steps(id,scan_id,step_type,status,attempt,started_at,finished_at,details)
VALUES (gen_random_uuid(),'$scan','ANALYSIS','SUCCEEDED',1,now(),now(),jsonb_build_object('policyDecision','CDR_ELIGIBLE','detection','CLEAN','sourceSha256','$src_hash','textExtraction','$summary'::jsonb));
INSERT INTO signature_verifications(id,scan_id,attempt,source_sha256,state,policy_decision,preserve_original,signature_count,report)
VALUES (gen_random_uuid(),'$scan',1,'$src_hash','UNSIGNED','CDR_REQUIRED',false,0,'{}');
INSERT INTO artifacts(id,scan_id,kind,storage_object,sha256,bytes,state) VALUES ('cdr-${scan:5}-1','$scan','CDR_OUTPUT','$out','$out_hash',$bytes,'SANITIZED_STAGED');
INSERT INTO cdr_results(id,scan_id,attempt,status,source_sha256,output_artifact_id,output_sha256,output_bytes,image_digest)
VALUES (gen_random_uuid(),'$scan',1,'SUCCEEDED','$src_hash','cdr-${scan:5}-1','$out_hash',$bytes,'sha256:seeded');
INSERT INTO final_validations(id,scan_id,attempt,artifact_id,source_sha256,output_sha256,verdict,detection,policy_decision,failure_codes,policy_version,engines,report_sha256)
VALUES (gen_random_uuid(),'$scan',1,'cdr-${scan:5}-1','$src_hash','$out_hash','PASS','NO_THREATS_DETECTED','ALLOW_SANITIZED','[]','final-policy-1','{}','$out_hash');
UPDATE artifacts SET state='FINAL_VALIDATED' WHERE id='cdr-${scan:5}-1';
UPDATE scan_jobs SET status='AWAITING_ARCHIVE',detection='NO_THREATS_DETECTED',policy='ALLOW_SANITIZED',artifact_state='FINAL_VALIDATED' WHERE id='$scan';
INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (gen_random_uuid(),'SCAN','$scan','ARCHIVE_QUEUED',jsonb_build_object('scanId','$scan'));
SQL
}
