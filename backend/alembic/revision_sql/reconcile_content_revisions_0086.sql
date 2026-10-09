CREATE OR REPLACE FUNCTION reconcile_content_revisions(target_content_id uuid DEFAULT NULL)
RETURNS TABLE(created_count integer, cleared_count integer, unchanged_count integer)
LANGUAGE plpgsql AS $$
DECLARE
    item record; active record; new_revision_id uuid; next_edition integer; eligible boolean;
BEGIN
    created_count := 0; cleared_count := 0; unchanged_count := 0;
    FOR item IN
        SELECT ci.*, p.source_snapshot_hash AS approved_source_snapshot_hash
        FROM content_items ci
        LEFT JOIN hospital_content_philosophies p
          ON p.id = ci.content_philosophy_id
         AND p.hospital_id = ci.hospital_id
         AND p.status = 'APPROVED'
        WHERE target_content_id IS NULL OR ci.id = target_content_id
        ORDER BY ci.id
        FOR UPDATE OF ci
    LOOP
        SELECT * INTO active FROM content_revisions WHERE id = item.active_revision_id;

        IF item.status <> 'PUBLISHED' OR item.published_at IS NULL THEN
            IF item.active_revision_id IS NOT NULL THEN
                UPDATE content_items
                SET active_revision_id = NULL
                WHERE id = item.id
                  AND content_revision = item.content_revision
                  AND active_revision_id = item.active_revision_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'content revision reconciliation CAS failed for %', item.id;
                END IF;
                cleared_count := cleared_count + 1;
            ELSE
                unchanged_count := unchanged_count + 1;
            END IF;
            CONTINUE;
        END IF;

        IF FOUND AND active.approval_status = 'HISTORICAL_PUBLICATION' THEN
            unchanged_count := unchanged_count + 1;
            CONTINUE;
        END IF;

        eligible := btrim(coalesce(item.title, '')) <> ''
            AND btrim(coalesce(item.body, '')) <> ''
            AND item.essence_status = 'ALIGNED'
            AND item.content_philosophy_id IS NOT NULL
            AND item.approved_source_snapshot_hash IS NOT NULL
            AND jsonb_typeof(item.content_brief) = 'object'
            AND btrim(coalesce(item.content_brief ->> 'schema_version', '')) <> ''
            AND btrim(coalesce(item.content_brief ->> 'target_query', '')) <> ''
            AND jsonb_typeof(item.content_brief -> 'treatment_narrative') = 'object'
            AND item.content_brief -> 'treatment_narrative' ->> 'source' IN ('approved_philosophy', 'hospital_profile')
            AND btrim(coalesce(item.content_brief -> 'treatment_narrative' ->> 'angle', '')) <> ''
            AND jsonb_typeof(item.content_brief -> 'source_snapshot') = 'object'
            AND btrim(coalesce(item.content_brief -> 'source_snapshot' ->> 'hash', '')) <> ''
            AND jsonb_typeof(item.content_brief -> 'source_snapshot' -> 'source_asset_ids') = 'array'
            AND jsonb_path_exists(item.content_brief, '$.source_snapshot.source_asset_ids[*]')
            AND jsonb_typeof(item.essence_check_summary -> 'generation_provenance') = 'object'
            AND (jsonb_path_exists(item.essence_check_summary, '$.generation_provenance.evidence_note_ids[*]')
                 OR jsonb_path_exists(item.essence_check_summary, '$.generation_provenance.source_asset_ids[*]')
                 OR jsonb_path_exists(item.essence_check_summary, '$.generation_provenance.evidence_source_asset_ids[*]'))
            AND jsonb_typeof(item.references_list) = 'array'
            AND jsonb_typeof(item.reference_checks) = 'array'
            AND ((jsonb_array_length(item.references_list) = 0
                  AND jsonb_array_length(item.reference_checks) = 0)
                 OR (jsonb_array_length(item.references_list) > 0
                     AND jsonb_array_length(item.reference_checks) > 0
                     AND NOT EXISTS (
                         SELECT 1 FROM jsonb_array_elements(item.references_list) ref
                         WHERE btrim(coalesce(ref ->> 'title', '')) = ''
                            OR btrim(coalesce(ref ->> 'url', '')) = ''
                            OR NOT EXISTS (
                                SELECT 1 FROM jsonb_array_elements(item.reference_checks) check_row
                                WHERE lower(coalesce(check_row ->> 'verdict', '')) = 'pass'
                                  AND btrim(coalesce(check_row ->> 'url', '')) = btrim(ref ->> 'url')
                            )
                     )))
            AND NOT coalesce((item.essence_check_summary ->> 'blocking')::boolean, false)
            AND NOT coalesce((item.essence_check_summary ->> 'authority_change')::boolean, false)
            AND NOT coalesce(item.essence_check_summary -> 'ai_review' ->> 'status' = 'UNAVAILABLE', false)
            AND NOT coalesce(
                item.essence_check_summary -> 'ai_review' ->> 'status' = 'REVISE'
                AND coalesce((item.essence_check_summary -> 'ai_review' ->> 'blocking')::boolean, true), false
            )
            AND (item.content_type::text = 'NOTICE' OR jsonb_array_length(item.references_list) > 0)
            AND (item.content_type::text <> 'FAQ' OR (
                right(btrim(coalesce(item.faq_question, '')), 1) = '?'
                AND btrim(coalesce(item.faq_answer_summary, '')) <> ''
            ));

        IF FOUND
           AND active.content_item_id = item.id
           AND active.legacy_content_revision = item.content_revision
           AND active.title = item.title
           AND active.body = item.body
           AND active.meta_description IS NOT DISTINCT FROM item.meta_description
           AND active.faq_question IS NOT DISTINCT FROM item.faq_question
           AND active.faq_answer_summary IS NOT DISTINCT FROM item.faq_answer_summary
           AND active.references_list IS NOT DISTINCT FROM item.references_list
           AND active.reference_checks IS NOT DISTINCT FROM item.reference_checks
        THEN
            unchanged_count := unchanged_count + 1;
            CONTINUE;
        END IF;

        IF NOT eligible THEN
            unchanged_count := unchanged_count + 1;
            CONTINUE;
        END IF;

        SELECT coalesce(max(edition_no), 0) + 1 INTO next_edition
        FROM content_revisions WHERE content_item_id = item.id;
        new_revision_id := (
            substr(md5(item.id::text || ':' || next_edition::text), 1, 8) || '-' || substr(md5(item.id::text || ':' || next_edition::text), 9, 4) || '-' ||
            substr(md5(item.id::text || ':' || next_edition::text), 13, 4) || '-' || substr(md5(item.id::text || ':' || next_edition::text), 17, 4) || '-' ||
            substr(md5(item.id::text || ':' || next_edition::text), 21, 12)
        )::uuid;
        INSERT INTO content_revisions (
            id, content_item_id, edition_no, legacy_content_revision, title, body, meta_description,
            faq_question, faq_answer_summary, references_list, reference_checks,
            generation_philosophy_id, last_reviewed_philosophy_id, generated_at, reviewed_at,
            reviewed_by, source_snapshot, generation_provenance, source_snapshot_hash,
            source_fingerprint, approval_hash, approval_status, approved_at, approved_by
        ) VALUES (
            new_revision_id, item.id, next_edition, item.content_revision, item.title, item.body,
            item.meta_description, item.faq_question, item.faq_answer_summary, item.references_list,
            item.reference_checks, item.generation_philosophy_id, item.last_reviewed_philosophy_id,
            item.generated_at, item.post_publish_reviewed_at, item.post_publish_reviewed_by,
            item.content_brief, item.essence_check_summary -> 'generation_provenance',
            item.approved_source_snapshot_hash,
            encode(digest(convert_to(concat_ws('|', item.content_brief::text,
                (item.essence_check_summary -> 'generation_provenance')::text,
                item.reference_checks::text), 'UTF8'), 'sha256'), 'hex'),
            encode(digest(convert_to(concat_ws('|', item.title, item.body,
                coalesce(item.meta_description, ''), coalesce(item.faq_question, ''),
                coalesce(item.faq_answer_summary, ''), item.references_list::text), 'UTF8'), 'sha256'), 'hex'),
            'APPROVED', item.published_at, item.published_by
        );
        UPDATE content_items SET active_revision_id = new_revision_id
        WHERE id = item.id AND content_revision = item.content_revision;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'content revision reconciliation CAS failed for %', item.id;
        END IF;
        created_count := created_count + 1;
    END LOOP;
    RETURN NEXT;
END;
$$;
