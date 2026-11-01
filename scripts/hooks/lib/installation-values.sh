#!/bin/bash
# shellcheck shell=bash
# installation-values.sh - the ONE installation-value guard, shared by the
# pre-commit, commit-msg and pre-push hooks (sourced, never run on its own).
#
# The real values of the installation this checkout belongs to (VM names and
# addresses, hostnames, storage paths) are keys in the gitignored
# .claude/local-scrub-map.txt, one `key==>replacement` per line: a literal
# (optionally `literal:`-prefixed, matched case-insensitively) or
# `regex:<perl regex>` - the format git filter-repo --replace-text reads.
# `glob:` keys are refused. A map with no keys, an invalid regex, or a map
# that is not a readable regular file fails closed. No map anywhere means this
# checkout has no installation values to guard, so nothing is checked.
#
# Map loading and matching live here only, so the three hooks cannot drift.
# scripts/install-hooks.sh installs this file as .git/hooks/lib/ next to the
# hooks; each hook sources it from its own directory.

# The map: this checkout's, else the main worktree's (a linked worktree has no
# copy of the gitignored file, and must not pass unchecked because of it).
# Prints the path. 0 = found, 1 = no map anywhere, 2 = git rev-parse failed.
iv_find_scrub_map() {
    local top common candidate
    top=$(git rev-parse --show-toplevel) || return 2
    common=$(git rev-parse --path-format=absolute --git-common-dir) || return 2
    for candidate in "$top/.claude/local-scrub-map.txt" "${common%/}/../.claude/local-scrub-map.txt"; do
        if [[ -e "$candidate" || -L "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

# iv_locate_map: iv_find_scrub_map plus the regular-file check. Prints the path
# (also on 3, for the caller's message). 0 = usable, 1 = no map (nothing to
# check), 2 = git rev-parse failed, 3 = not a readable regular file.
iv_locate_map() {
    local map rc=0
    map=$(iv_find_scrub_map) || rc=$?
    case "$rc" in
        0) ;;
        1) return 1 ;;
        *) return 2 ;;
    esac
    printf '%s\n' "$map"
    if [[ ! -f "$map" || ! -r "$map" ]]; then
        return 3
    fi
    return 0
}

# iv_match HOOK MODE MAP [ARG]: load MAP, then judge one kind of input. Every
# hit is reported on stderr as "<HOOK>: <where> contains an installation value"
# (the value itself is never printed). Exit status: 0 = clean, 1 = a value was
# found, 2 = the map or the input could not be read (callers fail closed).
#   staged         pre-commit: NUL-separated paths in $SCRUB_NAMES, `git diff
#                  --cached -U0` on stdin (added lines only)
#   message FILE [COMMENT [CUT]]
#                  commit-msg: the message file, minus lines starting with
#                  COMMENT (git's comment string; default '#'; empty = no line
#                  is a comment) and everything from the `git commit -v` cut
#                  line written with CUT (default '#'; empty = no cut line)
#   commits        pre-push: stdin lists `ref <name>`, `tag <sha>` and
#                  `commit <sha>` lines; each commit's message, the paths it
#                  adds or changes and its added lines are judged, merges by
#                  their combined (--cc) diff. A tag is judged by its name and
#                  message, and so is every tag it points to (a tag of a tag);
#                  a merge of a signed tag also by the tag's name and message
#                  that git embeds in the commit's `mergetag` header. Author,
#                  committer and tagger lines and commit signatures are not
#                  judged.
#                  Labels name the commit or tag (short sha) and mask any value
#                  inside a path or ref name.
iv_match() {
    local hook="$1" mode="$2" map="$3"
    shift 3
    SCRUB_MAP="$map" perl -e '
        use strict;
        my ($hook, $mode, @args) = @ARGV;
        open my $m, "<", $ENV{SCRUB_MAP} or do { print STDERR "$hook: cannot read scrub map: $!\n"; exit 2 };
        my @keys;
        while (my $line = <$m>) {
            $line =~ s/^\x{EF}\x{BB}\x{BF}// if $. == 1;
            $line =~ s/\r?\n\z//;
            next if $line =~ /^\s*(#|$)/;
            my ($k) = split /==>/, $line, 2;
            $k =~ s/^\s+|\s+$//g;
            if ($k =~ s/^regex://) {
                my $re = eval { qr/$k/ } or do { print STDERR "$hook: scrub map line $.: invalid regex\n"; exit 2 };
                push @keys, $re;
            } elsif ($k =~ /^glob:/) {
                print STDERR "$hook: scrub map line $.: glob: is unsupported (use a literal or regex:)\n"; exit 2;
            } else {
                $k =~ s/^literal://;
                push @keys, qr/\Q$k\E/i;
            }
        }
        if (!@keys) { print STDERR "$hook: scrub map holds no keys\n"; exit 2 }

        # Labels of the hooks that run after the commit exists name paths and
        # refs with any value masked; pre-commit keeps its historical labels.
        my $mask = $mode ne "staged";
        my $masked = sub {
            my ($label) = @_;
            if ($mask) { $label =~ s/$_/<installation value>/g for @keys }
            return $label;
        };
        my $bad = 0;
        my $hit = sub {
            my ($text, $where) = @_;
            for my $re (@keys) {
                if ($text =~ $re) { print STDERR "$hook: $where contains an installation value\n"; $bad = 1; return }
            }
        };

        # Added lines of a unified (or, for $parents > 1, combined) diff. In a
        # combined diff a line the merge itself introduces is "+" in every
        # parent column; a line "+" in only some columns came from a parent,
        # whose own commits are judged on their own.
        my $scan_diff = sub {
            my ($fh, $prefix, $parents) = @_;
            my $cols = $parents > 1 ? $parents : 1;
            my ($file, $inhunk) = ("", 0);
            while (my $l = <$fh>) {
                if ($l =~ /^diff --(?:git|cc|combined) /) { $inhunk = 0; next }
                if (!$inhunk && $l =~ /^\+\+\+ (?:b\/)?(.*)/) { $file = $1; next }
                if ($l =~ /^@@/) { $inhunk = 1; next }
                next unless $inhunk && substr($l, 0, $cols) eq ("+" x $cols);
                $hit->($l, $prefix . "added line in " . $masked->($file));
            }
        };

        if ($mode eq "staged") {
            { local $/ = "\0"; open my $n, "<", $ENV{SCRUB_NAMES} or exit 2;
              while (my $path = <$n>) { chomp $path; $hit->($path, "staged path $path") } }
            $scan_diff->(\*STDIN, "", 1);
            exit $bad;
        }

        if ($mode eq "message") {
            my $comment = defined $args[1] ? $args[1] : "#";
            my $cut = defined $args[2] ? $args[2] : "#";
            open my $f, "<", $args[0] or do { print STDERR "$hook: cannot read the commit message: $!\n"; exit 2 };
            while (my $l = <$f>) {
                last if $cut ne "" && $l =~ /^\Q$cut\E -{24} >8 -{24}\r?$/;
                next if $comment ne "" && index($l, $comment) == 0;
                $hit->($l, "commit message line $.");
            }
            exit $bad;
        }

        if ($mode ne "commits") { print STDERR "$hook: unknown check mode $mode\n"; exit 2 }

        # diff-tree is plumbing: colour, diff.noprefix/mnemonicPrefix, external
        # diff drivers, textconv and rename detection are never applied to it.
        # A `-diff` attribute still is ("Binary files differ"), hence --text.
        # The prefixes are pinned because the parser strips "b/"; core.quotePath
        # off keeps non-ASCII paths raw so their labels can be masked.
        my @git = ("git", "-c", "core.quotePath=false");
        my @diffopts = ("--text", "--src-prefix=a/", "--dst-prefix=b/");
        my $git_read = sub {
            my ($what, @cmd) = @_;
            open my $fh, "-|", @git, @cmd or do { print STDERR "$hook: cannot run git ($what)\n"; exit 2 };
            return $fh;
        };
        my $git_done = sub {
            my ($fh, $what) = @_;
            close $fh or do { print STDERR "$hook: git failed ($what)\n"; exit 2 };
        };
        # Raw object text minus its headers (which end at the first empty line).
        my $object = sub {
            my ($type, $sha) = @_;
            my $fh = $git_read->("cat-file $sha", "cat-file", $type, $sha);
            my (@headers, @body, $inbody);
            while (my $l = <$fh>) {
                if ($inbody) { push @body, $l; next }
                if ($l eq "\n") { $inbody = 1; next }
                push @headers, $l;
            }
            $git_done->($fh, "cat-file $sha");
            return (\@headers, \@body);
        };

        # A tag object (its own, or one embedded in a merge as a `mergetag`
        # header): its name and its whole message. A signature block stays in:
        # a line shaped like one must not hide the lines after it.
        my $judge_tag = sub {
            my ($headers, $body, $label) = @_;
            for my $l (@$headers) { $hit->($l, "$label: name") if $l =~ /^tag / }
            my $n = 0;
            for my $l (@$body) {
                $n++;
                $hit->($l, "$label: message line $n");
            }
        };

        my %seen;
        while (my $item = <STDIN>) {
            chomp $item;
            next if $item eq "";
            my ($kind, $arg) = split / /, $item, 2;
            if ($kind eq "ref") {
                $hit->($arg, "pushed ref " . $masked->($arg));
            } elsif ($kind eq "tag") {
                # Peel a tag of a tag: every tag in the chain is published.
                my $sha = $arg;
                while (defined $sha && !$seen{"tag $sha"}++) {
                    my ($h, $body) = $object->("tag", $sha);
                    $judge_tag->($h, $body, "tag " . substr($sha, 0, 12));
                    my ($type) = map { /^type (\S+)/ ? $1 : () } @$h;
                    my ($next) = map { /^object (\S+)/ ? $1 : () } @$h;
                    $sha = defined $type && $type eq "tag" ? $next : undef;
                }
            } elsif ($kind eq "commit") {
                next if $seen{$arg}++;
                my $short = substr($arg, 0, 12);
                my ($h, $body) = $object->("commit", $arg);
                my $parents = grep { /^parent / } @$h;
                my $n = 0;
                for my $l (@$body) { $n++; $hit->($l, "commit $short: message line $n") }
                # `mergetag` headers: each a whole tag object, continuation
                # lines indented by one space.
                my (@tags, $cur);
                for my $l (@$h) {
                    if ($l =~ /^mergetag (.*)\z/s) { $cur = [$1]; push @tags, $cur }
                    elsif ($cur && $l =~ /^ (.*)\z/s) { push @$cur, $1 }
                    else { $cur = undef }
                }
                my $t = 0;
                for my $tag (@tags) {
                    $t++;
                    my (@th, @tb, $intb);
                    for my $l (@$tag) {
                        if ($intb) { push @tb, $l; next }
                        if ($l eq "\n") { $intb = 1; next }
                        push @th, $l;
                    }
                    $judge_tag->(\@th, \@tb, "commit $short: merged tag $t");
                }
                my @shape = $parents > 1 ? ("-c") : ("--root");
                {
                    local $/ = "\0";
                    my $fh = $git_read->("paths of $short", "diff-tree", "-r", "-z", "--no-commit-id",
                        "--name-only", "--diff-filter=ACMRT", @shape, $arg);
                    while (my $path = <$fh>) { chomp $path; $hit->($path, "commit $short: path " . $masked->($path)) }
                    $git_done->($fh, "paths of $short");
                }
                my $fh = $git_read->("diff of $short", "diff-tree", "-r", "--no-commit-id", "-U0", @diffopts,
                    $parents > 1 ? ("--cc") : ("--root", "-p"), $arg);
                $scan_diff->($fh, "commit $short: ", $parents);
                $git_done->($fh, "diff of $short");
            } else {
                print STDERR "$hook: unknown item $kind\n"; exit 2;
            }
        }
        exit $bad;
    ' "$hook" "$mode" "$@"
}
