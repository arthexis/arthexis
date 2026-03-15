with Ada.Strings.Unbounded;

package body Arthexis.ORM is

   function Connect_SQLite (Path : String) return Database_Connection is
   begin
      --  TODO: Run PRAGMA foreign_keys = ON right after real SQLite connect.
      return (URI => Ada.Strings.Unbounded.To_Unbounded_String ("sqlite://" & Path));
   end Connect_SQLite;

   procedure Execute (Conn : in out Database_Connection; SQL : String) is
      pragma Unreferenced (Conn);
      pragma Unreferenced (SQL);
   begin
      --  TODO: Wire this to GNATCOLL.SQL.Exec in runtime integration.
      raise Program_Error with "Arthexis.ORM.Execute not yet implemented";
   end Execute;

   procedure Register_SQL_Function
     (Conn      : in out Database_Connection;
      Name      : String;
      Arity     : Natural;
      SQL_Body  : String) is
   begin
      if Name'Length = 0 then
         raise Constraint_Error with "SQL function name cannot be blank";
      end if;

      if Arity > 127 then
         raise Constraint_Error with "SQL function arity out of supported range";
      end if;

      Execute (Conn, SQL_Body);
   end Register_SQL_Function;

   procedure Register_Trigger
     (Conn      : in out Database_Connection;
      Name      : String;
      SQL_Body  : String) is
   begin
      if Name'Length = 0 then
         raise Constraint_Error with "SQLite trigger name cannot be blank";
      end if;

      Execute (Conn, SQL_Body);
   end Register_Trigger;

end Arthexis.ORM;
